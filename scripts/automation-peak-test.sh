#!/usr/bin/env bash
# Automation Worker 本地联调脚本：模拟四个不同优先级的任务同时涌入。
#
# 前提：本地已经用 docker compose up -d 跑起来 automation、automation-worker、
# redis、gateway。
#
# 用法：
#   ./scripts/automation-peak-test.sh
#
# 做的事：
#   1. 依次调用四次 /v1/requests/preview，覆盖四档 urgency × 四个场景
#   2. 几乎同时提交四个 /v1/jobs（模拟高峰涌入），各自带不同的 Idempotency-Key
#   3. 轮询直到全部结束，然后按 started_at 打印**真实的调度顺序**
#
# 关注点不是"任务能不能跑完"，而是 Worker 是否按 effective_priority 调度：
# critical 应当先于 low 被领取，而 policy_version 必须在整个生命周期里保持不变。

set -euo pipefail

AUTOMATION_URL="${AUTOMATION_URL:-http://localhost:8020}"
POLL_SECONDS=2
MAX_POLLS=30

# 四档 urgency 配四个场景，与 contracts/policy-examples.json 的 scenarios 对齐。
# 提交顺序刻意与优先级相反（low 先提交），这样"critical 仍然先被执行"才有说服力。
INTENT_SPECS=(
  "low|marketing_batch|marketing|Draft next week's campaign copy"
  "normal|finance_summary|finance|Summarize this month's spend by provider"
  "high|customer_escalation|support|Customer reports repeated 500s on checkout"
  "critical|production_incident|engineering|Checkout API is down for all regions"
)

echo "Automation peak-load smoke test — ${AUTOMATION_URL}"
echo "----------------------------------------"

job_ids=()

build_intent() {
  local urgency="$1" scenario="$2" department="$3" prompt="$4"
  python3 -c '
import json
import sys

urgency, scenario, department, prompt = sys.argv[1:5]
# finance_summary 的 privacy 被护栏锁死为 high，这里按场景给出合规的偏好。
privacy = "high" if scenario == "finance_summary" else "standard"
quality = {"critical": "high", "high": "high", "normal": "balanced", "low": "cheap"}[urgency]
print(json.dumps({
    "employee": "peak-test",
    "department": department,
    "scenario": scenario,
    "urgency": urgency,
    "prompt": prompt,
    "preferences": {
        "quality": quality,
        "privacy": privacy,
        "max_cost_usd": 0.01,
        "latency_target_ms": 3000,
    },
}))
' "$urgency" "$scenario" "$department" "$prompt"
}

for spec in "${INTENT_SPECS[@]}"; do
  IFS='|' read -r urgency scenario department prompt <<<"$spec"
  intent="$(build_intent "$urgency" "$scenario" "$department" "$prompt")"

  preview_resp=$(curl -sS -X POST "${AUTOMATION_URL}/v1/requests/preview" \
    -H "Content-Type: application/json" \
    -d "$intent")

  if ! preview_id=$(echo "$preview_resp" | python3 -c "
import json
import sys

body = json.load(sys.stdin)
if 'preview_id' not in body:
    sys.stderr.write(f'preview rejected: {json.dumps(body)[:300]}\n')
    raise SystemExit(1)
print(body['preview_id'])
"); then
    echo "预览失败，终止。" >&2
    exit 1
  fi

  read -r score version <<<"$(echo "$preview_resp" | python3 -c "
import json
import sys

body = json.load(sys.stdin)
print(body['priority']['initial_score'], body.get('policy_version', '-'))
")"

  job_resp=$(curl -sS -X POST "${AUTOMATION_URL}/v1/jobs" \
    -H "Content-Type: application/json" \
    -H "Idempotency-Key: peak-test-${urgency}-$(date +%s)-$RANDOM" \
    -d "{\"preview_id\": \"${preview_id}\", \"confirmed\": true}")

  job_id=$(echo "$job_resp" | python3 -c "import sys, json; print(json.load(sys.stdin)['job_id'])")
  job_ids+=("$job_id")
  printf "[submit] %-8s %-22s score=%-4s policy_version=%-3s job=%s\n" \
    "$urgency" "$scenario" "$score" "$version" "$job_id"
done

echo "----------------------------------------"
echo "四个任务已提交，开始轮询（每 ${POLL_SECONDS}s，最多 ${MAX_POLLS} 次）..."

for _ in $(seq 1 "$MAX_POLLS"); do
  all_done=true
  for job_id in "${job_ids[@]}"; do
    status=$(curl -sS "${AUTOMATION_URL}/v1/jobs/${job_id}" \
      | python3 -c "import sys, json; print(json.load(sys.stdin)['status'])")
    if [[ "$status" != "completed" && "$status" != "failed" ]]; then
      all_done=false
    fi
  done
  if $all_done; then
    break
  fi
  sleep "$POLL_SECONDS"
done

if ! $all_done; then
  echo "----------------------------------------"
  echo "超过最大轮询次数，仍有任务没结束，请检查 Worker 是否正常运行。" >&2
  exit 1
fi

echo "----------------------------------------"
echo "实际调度顺序（按 started_at 排序，不是提交顺序）："
echo

for job_id in "${job_ids[@]}"; do
  curl -sS "${AUTOMATION_URL}/v1/jobs/${job_id}"
  printf '\n'
done | python3 -c '
import json
import sys

records = [json.loads(line) for line in sys.stdin if line.strip()]
# started_at 为空的排最后，避免 None 参与比较。
records.sort(key=lambda r: (r.get("started_at") is None, r.get("started_at") or ""))

header = f'"'"'{"#":<3}{"urgency":<10}{"score":<7}{"policy":<8}{"wait_s":<8}{"status":<11}{"job_id"}'"'"'
print(header)
print("-" * len(header))

failures = []
for index, record in enumerate(records, start=1):
    urgency = record["priority"]["class"]
    score = record["priority"]["initial_score"]
    version = record.get("policy_version")
    status = record["status"]
    if record.get("started_at") and record.get("created_at"):
        from datetime import datetime
        started = datetime.fromisoformat(record["started_at"])
        created = datetime.fromisoformat(record["created_at"])
        wait = f"{(started - created).total_seconds():.1f}"
    else:
        wait = "-"
    print(f"{index:<3}{urgency:<10}{score:<7}{str(version):<8}{wait:<8}{status:<11}{record['"'"'job_id'"'"']}")
    if status != "completed":
        failures.append((record["job_id"], status))

print()
order = [record["priority"]["class"] for record in records]
if order and order[0] == "critical":
    print("OK   critical 最先被领取（调度按 effective_priority 生效）")
else:
    print(f"WARN 首个被领取的是 {order[0] if order else "?"}，不是 critical——检查队列策略")

versions = {record.get("policy_version") for record in records}
if len(versions) == 1 and None not in versions:
    print(f"OK   四个任务的 policy_version 一致且非空：{versions.pop()}")
else:
    print(f"WARN policy_version 不一致或缺失：{sorted(str(v) for v in versions)}")

if failures:
    print(f"WARN 有任务未 completed：{failures}")
    raise SystemExit(1)
print("OK   四个任务全部 completed")
'
