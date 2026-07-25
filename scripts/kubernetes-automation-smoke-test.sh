#!/usr/bin/env bash
# Verify the deployed Automation API -> Redis queue -> Worker -> Gateway path.
# Run after port-forwarding the private Automation API and Worker metrics port.

set -uo pipefail

AUTOMATION_URL="${AUTOMATION_URL:-http://localhost:8020}"
WORKER_METRICS_URL="${WORKER_METRICS_URL:-http://localhost:9000}"
TIMEOUT_SECONDS="${TIMEOUT_SECONDS:-60}"
POLL_INTERVAL_SECONDS="${POLL_INTERVAL_SECONDS:-1}"

PASS=0
FAIL=0

ok() {
  PASS=$((PASS + 1))
  echo "  OK  $1"
}

bad() {
  FAIL=$((FAIL + 1))
  echo "  FAIL  $1"
}

jget() {
  local expression="$1"
  python3 -c \
    'import json,sys; d=json.load(sys.stdin); print(eval("d" + sys.argv[1], {}, {"d": d}))' \
    "$expression"
}

echo "PolyGate Kubernetes Automation smoke test"
echo "Automation API: $AUTOMATION_URL"
echo "Worker metrics: $WORKER_METRICS_URL"

READY="$(curl -fsS --max-time 5 "$AUTOMATION_URL/ready" 2>/dev/null || true)"
if [ "$(printf '%s' "$READY" | jget "['status']" 2>/dev/null || true)" = "ready" ]; then
  ok "Automation API is ready and connected to Redis"
else
  bad "Automation readiness check failed: $READY"
fi

WORKER_HEALTH="$(
  curl -fsS --max-time 5 "$WORKER_METRICS_URL/health" 2>/dev/null || true
)"
if [ "$(printf '%s' "$WORKER_HEALTH" | jget "['status']" 2>/dev/null || true)" = "ok" ]; then
  ok "Automation Worker loop is healthy"
else
  bad "Automation Worker health check failed: $WORKER_HEALTH"
fi

INTENT='{"employee":"C-line smoke test","department":"engineering","scenario":"production_incident","urgency":"critical","prompt":"Return a short synthetic incident summary for the PolyGate deployment smoke test.","preferences":{"quality":"balanced","privacy":"high","max_cost_usd":0.01,"latency_target_ms":3000}}'
PREVIEW="$(
  curl -fsS --max-time 10 \
    --request POST \
    --header 'Content-Type: application/json' \
    --data "$INTENT" \
    "$AUTOMATION_URL/v1/requests/preview" \
    2>/dev/null || true
)"
PREVIEW_ID="$(printf '%s' "$PREVIEW" | jget "['preview_id']" 2>/dev/null || true)"
if [ -n "$PREVIEW_ID" ]; then
  ok "Automation compiled a prioritized Gateway request"
else
  bad "Automation preview failed: $PREVIEW"
fi

# Preview must be stamped with the policy version it was compiled against, as an
# integer >= 1 (not a string, not null) — Task 10 Step 2.
PREVIEW_POLICY_VERSION="$(
  printf '%s' "$PREVIEW" | python3 -c '
import json
import sys

value = json.load(sys.stdin).get("policy_version")
if not isinstance(value, int) or isinstance(value, bool) or value < 1:
    raise SystemExit(1)
print(value)
' 2>/dev/null || true
)"
if [ -n "$PREVIEW_POLICY_VERSION" ]; then
  ok "Preview carries an integer policy_version (v$PREVIEW_POLICY_VERSION)"
else
  bad "Preview policy_version is missing or not a positive integer: $PREVIEW"
fi

IDEMPOTENCY_KEY="k8s-automation-smoke-$(date +%s)-$$"
SUBMISSION="{\"preview_id\":\"$PREVIEW_ID\",\"confirmed\":true}"
JOB_ONE="$(
  curl -fsS --max-time 10 \
    --request POST \
    --header 'Content-Type: application/json' \
    --header "Idempotency-Key: $IDEMPOTENCY_KEY" \
    --data "$SUBMISSION" \
    "$AUTOMATION_URL/v1/jobs" \
    2>/dev/null || true
)"
JOB_TWO="$(
  curl -fsS --max-time 10 \
    --request POST \
    --header 'Content-Type: application/json' \
    --header "Idempotency-Key: $IDEMPOTENCY_KEY" \
    --data "$SUBMISSION" \
    "$AUTOMATION_URL/v1/jobs" \
    2>/dev/null || true
)"
JOB_ID_ONE="$(printf '%s' "$JOB_ONE" | jget "['job_id']" 2>/dev/null || true)"
JOB_ID_TWO="$(printf '%s' "$JOB_TWO" | jget "['job_id']" 2>/dev/null || true)"
if [ -n "$JOB_ID_ONE" ] && [ "$JOB_ID_ONE" = "$JOB_ID_TWO" ]; then
  ok "Redis-backed submission is idempotent"
else
  bad "Idempotent submission failed: first=$JOB_ONE second=$JOB_TWO"
fi

SUBMITTED_POLICY_VERSION="$(
  printf '%s' "$JOB_ONE" | python3 -c '
import json
import sys

value = json.load(sys.stdin).get("policy_version")
if not isinstance(value, int) or isinstance(value, bool) or value < 1:
    raise SystemExit(1)
print(value)
' 2>/dev/null || true
)"
if [ -n "$SUBMITTED_POLICY_VERSION" ] \
  && [ "$SUBMITTED_POLICY_VERSION" = "$PREVIEW_POLICY_VERSION" ]; then
  ok "Queued Job inherits the preview's policy_version (v$SUBMITTED_POLICY_VERSION)"
else
  bad "Job policy_version ($SUBMITTED_POLICY_VERSION) does not match preview ($PREVIEW_POLICY_VERSION)"
fi

# An unrelated read of the active policy while the job is in flight must not
# change the version already stamped on that job — Task 10 Step 2.
curl -fsS --max-time 5 "$AUTOMATION_URL/v1/policies/active" >/dev/null 2>&1 || true

status=""
job_body=""
deadline=$((SECONDS + TIMEOUT_SECONDS))
while [ "$SECONDS" -lt "$deadline" ] && [ -n "$JOB_ID_ONE" ]; do
  job_body="$(
    curl -fsS --max-time 5 \
      "$AUTOMATION_URL/v1/jobs/$JOB_ID_ONE" \
      2>/dev/null || true
  )"
  status="$(printf '%s' "$job_body" | jget "['status']" 2>/dev/null || true)"
  if [ "$status" = "completed" ] || [ "$status" = "failed" ]; then
    break
  fi
  sleep "$POLL_INTERVAL_SECONDS"
done

if [ "$status" = "completed" ]; then
  ok "Worker completed the queued request through Gateway"
else
  bad "Job did not complete successfully (status=$status): $job_body"
fi

COMPLETED_POLICY_VERSION="$(
  printf '%s' "$job_body" | jget "['policy_version']" 2>/dev/null || true
)"
if [ "$COMPLETED_POLICY_VERSION" = "$SUBMITTED_POLICY_VERSION" ] \
  && [ -n "$SUBMITTED_POLICY_VERSION" ]; then
  ok "Completed Job retains its submission policy_version (v$COMPLETED_POLICY_VERSION)"
else
  bad "Completed Job policy_version drifted: submitted=$SUBMITTED_POLICY_VERSION completed=$COMPLETED_POLICY_VERSION"
fi

METRICS="$(
  curl -fsS --max-time 5 "$WORKER_METRICS_URL/metrics" 2>/dev/null || true
)"
missing_metrics=()
for metric in \
  automation_worker_jobs_processed_total \
  automation_worker_jobs_failed_total \
  automation_worker_jobs_retried_total \
  automation_worker_job_duration_seconds \
  automation_worker_queue_depth \
  automation_worker_in_flight \
  automation_worker_queue_wait_seconds; do
  if ! printf '%s' "$METRICS" | grep -Fq "$metric"; then
    missing_metrics+=("$metric")
  fi
done

if [ "${#missing_metrics[@]}" -eq 0 ]; then
  ok "Worker exposes all required Prometheus metrics"
else
  bad "Worker metrics are missing: ${missing_metrics[*]}"
fi

echo
echo "$PASS passed, $FAIL failed"

if [ "$FAIL" -gt 0 ]; then
  exit 1
fi
