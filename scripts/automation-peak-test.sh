#!/usr/bin/env bash
# Automation Worker 本地联调脚本：模拟四个任务同时涌入的高峰场景。
#
# 前提：本地已经用 docker compose up -d 跑起来 automation、redis、gateway，
# 并且 Worker 也已经跑起来（同镜像，command 换成
# python -m automation.app.worker）。
#
# 用法：
#   ./scripts/automation-peak-test.sh
#
# 做的事：
#   1. 依次调用四次 /v1/requests/preview，拿到四个 preview_id
#   2. 几乎同时提交四个 /v1/jobs（模拟高峰涌入），各自带不同的
#      Idempotency-Key
#   3. 轮询 /v1/jobs，观察四个任务从 queued -> running -> completed/failed
#      的状态变化，把结果打印出来

set -euo pipefail

AUTOMATION_URL="${AUTOMATION_URL:-http://localhost:8020}"
JOB_COUNT=4
POLL_SECONDS=2
MAX_POLLS=30

echo "Automation peak-load smoke test — ${AUTOMATION_URL}"
echo "----------------------------------------"

job_ids=()

for i in $(seq 1 "$JOB_COUNT"); do
  preview_resp=$(curl -s -X POST "${AUTOMATION_URL}/v1/requests/preview" \
    -H "Content-Type: application/json" \
    -d "{\"raw_text\": \"peak test task ${i}\"}")

  preview_id=$(echo "$preview_resp" | python3 -c "import sys, json; print(json.load(sys.stdin)['preview_id'])")

  job_resp=$(curl -s -X POST "${AUTOMATION_URL}/v1/jobs" \
    -H "Content-Type: application/json" \
    -H "Idempotency-Key: peak-test-${i}-$(date +%s)" \
    -d "{\"preview_id\": \"${preview_id}\", \"confirmed\": true}")

  job_id=$(echo "$job_resp" | python3 -c "import sys, json; print(json.load(sys.stdin)['job_id'])")
  job_ids+=("$job_id")
  echo "[submit] task ${i} -> job_id=${job_id}"
done

echo "----------------------------------------"
echo "四个任务已提交，开始轮询状态（每 ${POLL_SECONDS}s 一次，最多 ${MAX_POLLS} 次）..."

for _ in $(seq 1 "$MAX_POLLS"); do
  all_done=true
  echo "----------------------------------------"
  for job_id in "${job_ids[@]}"; do
    status=$(curl -s "${AUTOMATION_URL}/v1/jobs/${job_id}" | python3 -c "import sys, json; print(json.load(sys.stdin)['status'])")
    echo "  ${job_id}: ${status}"
    if [[ "$status" != "completed" && "$status" != "failed" ]]; then
      all_done=false
    fi
  done
  if $all_done; then
    echo "----------------------------------------"
    echo "四个任务全部结束（completed 或 failed），测试完成。"
    exit 0
  fi
  sleep "$POLL_SECONDS"
done

echo "----------------------------------------"
echo "超过最大轮询次数，仍有任务没结束，请检查 Worker 是否正常运行。"
exit 1