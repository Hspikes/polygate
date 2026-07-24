#!/usr/bin/env bash
# Automation 峰值调度演示脚本
# ================================
# 目的：一次性提交一批"故意混合了不同 urgency / scenario"的任务，
# 然后实时打印它们被 Worker 处理的顺序，直观展示：
#   - 高优先级（critical / high）任务插到前面先执行
#   - 低优先级（normal / low）任务排在后面
#   - effective_priority = initial_score + 等待时间加成
#   - 防饥饿：低优先级等太久也会被救上来
#
# 用法：
#   本地：  ./scripts/automation-peak-demo.sh
#   云端：  AUTOMATION_URL=http://3.231.164.24:30080 ./scripts/automation-peak-demo.sh
#
# 前提：Automation API + Worker + Redis + Gateway 都在跑。

set -euo pipefail

AUTOMATION_URL="${AUTOMATION_URL:-http://localhost:8020}"
POLL_SECONDS="${POLL_SECONDS:-2}"
MAX_POLLS="${MAX_POLLS:-40}"

echo "=================================================================="
echo " Automation 峰值调度演示 — 目标: ${AUTOMATION_URL}"
echo "=================================================================="
echo

# 演示任务清单：故意混合不同 urgency 和 scenario，让优先级差异明显。
# 每一行格式: 标签|department|scenario|urgency|prompt
# 提交顺序故意"低优先级在前、高优先级在后"，好凸显 Worker 不是按提交顺序、
# 而是按优先级来挑选执行的。
JOBS=(
  "① 营销批处理(最低优先级)|marketing|marketing_batch|low|生成本月营销文案批次"
  "② 财务汇总(普通)|finance|finance_summary|normal|汇总上季度财报要点"
  "③ 客户升级(高)|support|customer_escalation|high|处理 VIP 客户升级工单"
  "④ 生产事故A(最高)|engineering|production_incident|critical|线上服务 5xx 激增，紧急排查"
  "⑤ 生产事故B(最高)|engineering|production_incident|critical|数据库连接池耗尽，紧急处理"
)

declare -A JOB_LABEL      # job_id -> 中文标签
declare -A JOB_URGENCY    # job_id -> urgency
job_ids=()

echo "【第一步】几乎同时提交 ${#JOBS[@]} 个混合优先级任务..."
echo "------------------------------------------------------------------"
for entry in "${JOBS[@]}"; do
  IFS='|' read -r label department scenario urgency prompt <<< "$entry"

  # 1) 先 preview，拿到 preview_id 和它算出来的 initial_score
  preview_resp=$(curl -s -X POST "${AUTOMATION_URL}/v1/requests/preview" \
    -H "Content-Type: application/json" \
    -d "{\"employee\":\"demo\",\"department\":\"${department}\",\"scenario\":\"${scenario}\",\"urgency\":\"${urgency}\",\"prompt\":\"${prompt}\",\"preferences\":{\"quality\":\"cheap\",\"privacy\":\"standard\",\"max_cost_usd\":1.0,\"latency_target_ms\":5000}}")

  preview_id=$(echo "$preview_resp" | python3 -c "import sys,json; print(json.load(sys.stdin)['preview_id'])")
  score=$(echo "$preview_resp" | python3 -c "import sys,json; print(json.load(sys.stdin)['priority']['initial_score'])")

  # 2) 提交任务
  job_resp=$(curl -s -X POST "${AUTOMATION_URL}/v1/jobs" \
    -H "Content-Type: application/json" \
    -H "Idempotency-Key: demo-$(date +%s%N)-${RANDOM}" \
    -d "{\"preview_id\":\"${preview_id}\",\"confirmed\":true}")

  job_id=$(echo "$job_resp" | python3 -c "import sys,json; print(json.load(sys.stdin)['job_id'])")
  job_ids+=("$job_id")
  JOB_LABEL[$job_id]="$label"
  JOB_URGENCY[$job_id]="$urgency"
  printf "  提交 %-22s urgency=%-9s initial_score=%-4s job=%s\n" "$label" "$urgency" "$score" "$job_id"
done

echo
echo "【第二步】轮询任务状态，观察 Worker 的处理顺序..."
echo "  （关注 started_at 的先后 —— 高优先级应该先进入 running）"
echo "------------------------------------------------------------------"

completion_order=()

for _ in $(seq 1 "$MAX_POLLS"); do
  all_done=true
  echo
  echo "  [$(date +%H:%M:%S)] 当前状态:"
  for job_id in "${job_ids[@]}"; do
    detail=$(curl -s "${AUTOMATION_URL}/v1/jobs/${job_id}")
    status=$(echo "$detail" | python3 -c "import sys,json; print(json.load(sys.stdin)['status'])")
    printf "    %-22s -> %s\n" "${JOB_LABEL[$job_id]}" "$status"

    # 第一次看到 completed 就记录完成顺序
    if [[ "$status" == "completed" || "$status" == "failed" ]]; then
      already_recorded=false
      for done_id in "${completion_order[@]:-}"; do
        [[ "$done_id" == "$job_id" ]] && already_recorded=true
      done
      $already_recorded || completion_order+=("$job_id")
    else
      all_done=false
    fi
  done

  if $all_done; then
    break
  fi
  sleep "$POLL_SECONDS"
done

echo
echo "=================================================================="
echo " 处理完成顺序（这就是优先级调度的效果）:"
echo "------------------------------------------------------------------"
rank=1
for job_id in "${completion_order[@]:-}"; do
  printf "  第 %s 个完成: %-22s (urgency=%s)\n" "$rank" "${JOB_LABEL[$job_id]}" "${JOB_URGENCY[$job_id]}"
  rank=$((rank + 1))
done
echo "=================================================================="
echo
echo "预期效果：尽管提交时低优先级在前，但 critical/high 的任务应该"
echo "更早被处理完 —— 这就是 effective_priority 优先级调度在起作用。"