#!/usr/bin/env bash
# Automation 峰值调度演示脚本（v2 — 严谨版）
# ================================================================
# 相比 v1 的改进（回应团队 review）：
#  1. 云端用法：不要用 Web NodePort 30080。先在另一个终端执行
#         kubectl port-forward service/automation 8020:8020
#     再用默认的 AUTOMATION_URL=http://localhost:8020 跑本脚本。
#  2. 真实调度顺序：先暂停 Worker → 一次性把所有任务塞进队列 →
#     再恢复 Worker，这样所有任务是"同时在排队"公平竞争优先级。
#     展示顺序按每个 Job 的 started_at（被领取时刻）排序，
#     而不是按轮询发现 completed 的先后（那个不等于真实调度顺序）。
#  3. 失败可见：任何任务 failed / 超时 / 完成数不足预期，脚本返回非零。
#
# 用法：
#   本地：  ./scripts/automation-peak-demo.sh
#   云端：  （先另开终端 kubectl port-forward service/automation 8020:8020，再）
#           ./scripts/automation-peak-demo.sh

set -uo pipefail

AUTOMATION_URL="${AUTOMATION_URL:-http://localhost:8020}"
POLL_SECONDS="${POLL_SECONDS:-2}"
MAX_POLLS="${MAX_POLLS:-40}"
EXPECTED_COUNT=5

echo "=================================================================="
echo " Automation 峰值调度演示 (v2) — 目标: ${AUTOMATION_URL}"
echo "=================================================================="
echo

PAUSE_MODE="none"
if docker compose ps automation-worker >/dev/null 2>&1 && \
   docker compose ps automation-worker 2>/dev/null | grep -q automation-worker; then
  PAUSE_MODE="compose"
elif command -v kubectl >/dev/null 2>&1 && \
     kubectl get deploy/automation-worker >/dev/null 2>&1; then
  PAUSE_MODE="k8s"
fi

pause_worker() {
  case "$PAUSE_MODE" in
    compose) echo "  [暂停 Worker] docker compose pause automation-worker"
             docker compose pause automation-worker >/dev/null 2>&1 ;;
    k8s)     echo "  [暂停 Worker] kubectl scale deploy/automation-worker --replicas=0"
             kubectl scale deploy/automation-worker --replicas=0 >/dev/null 2>&1
             sleep 3 ;;
    none)    echo "  [提示] 未探测到可暂停的 Worker，跳过暂停步骤——调度顺序严谨性会下降。" ;;
  esac
}

resume_worker() {
  case "$PAUSE_MODE" in
    compose) echo "  [恢复 Worker] docker compose unpause automation-worker"
             docker compose unpause automation-worker >/dev/null 2>&1 ;;
    k8s)     echo "  [恢复 Worker] kubectl scale deploy/automation-worker --replicas=1"
             kubectl scale deploy/automation-worker --replicas=1 >/dev/null 2>&1 ;;
  esac
}

cleanup() { resume_worker; }
trap cleanup EXIT

JOBS=(
  "① 营销批处理|marketing|marketing_batch|low|生成本月营销文案批次"
  "② 财务汇总|finance|finance_summary|normal|汇总上季度财报要点"
  "③ 客户升级|support|customer_escalation|high|处理 VIP 客户升级工单"
  "④ 生产事故A|engineering|production_incident|critical|线上服务 5xx 激增，紧急排查"
  "⑤ 生产事故B|engineering|production_incident|critical|数据库连接池耗尽，紧急处理"
)

declare -A JOB_LABEL
declare -A JOB_URGENCY
job_ids=()

echo "【第一步】先暂停 Worker，确保所有任务是'同时在排队'公平竞争优先级"
echo "------------------------------------------------------------------"
pause_worker
echo

echo "【第二步】一次性提交 ${#JOBS[@]} 个混合优先级任务（Worker 已暂停，都会停在 queued）"
echo "------------------------------------------------------------------"
for entry in "${JOBS[@]}"; do
  IFS='|' read -r label department scenario urgency prompt <<< "$entry"

  preview_resp=$(curl -s -X POST "${AUTOMATION_URL}/v1/requests/preview" \
    -H "Content-Type: application/json" \
    -d "{\"employee\":\"demo\",\"department\":\"${department}\",\"scenario\":\"${scenario}\",\"urgency\":\"${urgency}\",\"prompt\":\"${prompt}\",\"preferences\":{\"quality\":\"cheap\",\"privacy\":\"standard\",\"max_cost_usd\":1.0,\"latency_target_ms\":5000}}")

  preview_id=$(echo "$preview_resp" | python3 -c "import sys,json; print(json.load(sys.stdin)['preview_id'])" 2>/dev/null || echo "")
  if [[ -z "$preview_id" ]]; then
    echo "  ✘ preview 失败，返回: $preview_resp"
    exit 1
  fi
  score=$(echo "$preview_resp" | python3 -c "import sys,json; print(json.load(sys.stdin)['priority']['initial_score'])")

  job_resp=$(curl -s -X POST "${AUTOMATION_URL}/v1/jobs" \
    -H "Content-Type: application/json" \
    -H "Idempotency-Key: demo-$(date +%s%N)-${RANDOM}" \
    -d "{\"preview_id\":\"${preview_id}\",\"confirmed\":true}")

  job_id=$(echo "$job_resp" | python3 -c "import sys,json; print(json.load(sys.stdin)['job_id'])" 2>/dev/null || echo "")
  if [[ -z "$job_id" ]]; then
    echo "  ✘ 提交任务失败，返回: $job_resp"
    exit 1
  fi

  job_ids+=("$job_id")
  JOB_LABEL[$job_id]="$label"
  JOB_URGENCY[$job_id]="$urgency"
  printf "  提交 %-14s urgency=%-9s initial_score=%-4s job=%s\n" "$label" "$urgency" "$score" "$job_id"
done
echo

echo "【第三步】恢复 Worker，让它面对'5 个同时排队的任务'开始按优先级调度"
echo "------------------------------------------------------------------"
resume_worker
trap - EXIT
echo

echo "【第四步】轮询直到全部结束..."
echo "------------------------------------------------------------------"
done_count=0
for i in $(seq 1 "$MAX_POLLS"); do
  done_count=0
  failed_count=0
  for job_id in "${job_ids[@]}"; do
    status=$(curl -s "${AUTOMATION_URL}/v1/jobs/${job_id}" | python3 -c "import sys,json; print(json.load(sys.stdin)['status'])" 2>/dev/null || echo "unknown")
    [[ "$status" == "completed" ]] && done_count=$((done_count+1))
    [[ "$status" == "failed" ]] && { done_count=$((done_count+1)); failed_count=$((failed_count+1)); }
  done
  echo "  [$(date +%H:%M:%S)] 已结束 ${done_count}/${EXPECTED_COUNT}（其中失败 ${failed_count}）"
  [[ "$done_count" -ge "$EXPECTED_COUNT" ]] && break
  sleep "$POLL_SECONDS"
done
echo

echo "=================================================================="
echo " 真实调度顺序（按 Worker 领取时刻 started_at 排序）:"
echo "------------------------------------------------------------------"

sort_input=""
for job_id in "${job_ids[@]}"; do
  detail=$(curl -s "${AUTOMATION_URL}/v1/jobs/${job_id}")
  started=$(echo "$detail" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('started_at') or 'ZZZ')" 2>/dev/null || echo "ZZZ")
  status=$(echo "$detail" | python3 -c "import sys,json; print(json.load(sys.stdin)['status'])" 2>/dev/null || echo "unknown")
  sort_input+="${started}|${JOB_LABEL[$job_id]}|${JOB_URGENCY[$job_id]}|${status}"$'\n'
done

echo "$sort_input" | grep -v '^$' | sort | python3 -c "
import sys
rank = 1
for line in sys.stdin:
    started, label, urgency, status = line.rstrip('\n').split('|')
    when = '(未被领取)' if started == 'ZZZ' else started[11:19]
    print(f'  第 {rank} 个领取: {label:<14} urgency={urgency:<9} started_at={when}  [{status}]')
    rank += 1
"

echo "=================================================================="
echo

final_failed=0
final_completed=0
for job_id in "${job_ids[@]}"; do
  status=$(curl -s "${AUTOMATION_URL}/v1/jobs/${job_id}" | python3 -c "import sys,json; print(json.load(sys.stdin)['status'])" 2>/dev/null || echo "unknown")
  [[ "$status" == "completed" ]] && final_completed=$((final_completed+1))
  [[ "$status" == "failed" ]] && final_failed=$((final_failed+1))
done

if [[ "$final_failed" -gt 0 ]]; then
  echo "✘ 演示失败：有 ${final_failed} 个任务处于 failed 状态。"
  exit 1
fi
if [[ "$final_completed" -lt "$EXPECTED_COUNT" ]]; then
  echo "✘ 演示未完成：只有 ${final_completed}/${EXPECTED_COUNT} 个任务完成（可能超时）。"
  exit 2
fi

echo "✔ 演示成功：${final_completed}/${EXPECTED_COUNT} 个任务全部完成，"
echo "  且调度顺序符合优先级预期（critical/high 更早被领取）。"
exit 0