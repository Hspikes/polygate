#!/usr/bin/env bash
# Verify the frozen Automation API skeleton without calling any Provider.

set -uo pipefail

AUTOMATION_URL="${AUTOMATION_URL:-http://localhost:8020}"
PASS=0
FAIL=0

ok() { PASS=$((PASS + 1)); echo "  OK  $1"; }
bad() { FAIL=$((FAIL + 1)); echo "  FAIL  $1"; }

jget() {
  local expression="$1"
  python3 -c 'import json,sys; d=json.load(sys.stdin); print(eval("d" + sys.argv[1], {}, {"d": d}))' "$expression"
}

echo "PolyGate Automation skeleton smoke test"
echo "Automation: $AUTOMATION_URL"

HEALTH=$(curl -fsS --max-time 5 "$AUTOMATION_URL/health" 2>/dev/null || true)
if [ "$(printf '%s' "$HEALTH" | jget "['status']" 2>/dev/null || true)" = "ok" ]; then
  ok "Automation is healthy"
else
  bad "Automation health check failed: $HEALTH"
fi

TEMPLATES=$(curl -fsS --max-time 5 "$AUTOMATION_URL/v1/templates" 2>/dev/null || true)
TEMPLATE_COUNT=$(printf '%s' "$TEMPLATES" | python3 -c 'import json,sys; print(len(json.load(sys.stdin)))' 2>/dev/null || true)
if [ "$TEMPLATE_COUNT" = "4" ] && printf '%s' "$TEMPLATES" | grep -q 'finance_summary'; then
  ok "Four business templates are available"
else
  bad "Expected four business templates: $TEMPLATES"
fi

INTENT='{"employee":"Demo Finance User","department":"finance","scenario":"finance_summary","urgency":"normal","prompt":"Summarize this synthetic finance document.","preferences":{"quality":"balanced","privacy":"standard","max_cost_usd":0.005,"latency_target_ms":3000}}'
PREVIEW=$(curl -fsS --max-time 5 -X POST "$AUTOMATION_URL/v1/requests/preview" -H 'Content-Type: application/json' -d "$INTENT" 2>/dev/null || true)
PREVIEW_ID=$(printf '%s' "$PREVIEW" | jget "['preview_id']" 2>/dev/null || true)
PRIVACY=$(printf '%s' "$PREVIEW" | jget "['gateway_request']['polygate']['privacy']" 2>/dev/null || true)
if [ -n "$PREVIEW_ID" ] && [ "$PRIVACY" = "high" ]; then
  ok "Preview compiles the request and enforces Finance privacy"
else
  bad "Preview or privacy policy failed: $PREVIEW"
fi

IDEMPOTENCY_KEY="smoke-$(date +%s)-$$"
SUBMISSION="{\"preview_id\":\"$PREVIEW_ID\",\"confirmed\":true}"
JOB1=$(curl -fsS --max-time 5 -X POST "$AUTOMATION_URL/v1/jobs" -H 'Content-Type: application/json' -H "Idempotency-Key: $IDEMPOTENCY_KEY" -d "$SUBMISSION" 2>/dev/null || true)
JOB2=$(curl -fsS --max-time 5 -X POST "$AUTOMATION_URL/v1/jobs" -H 'Content-Type: application/json' -H "Idempotency-Key: $IDEMPOTENCY_KEY" -d "$SUBMISSION" 2>/dev/null || true)
JOB_ID_1=$(printf '%s' "$JOB1" | jget "['job_id']" 2>/dev/null || true)
JOB_ID_2=$(printf '%s' "$JOB2" | jget "['job_id']" 2>/dev/null || true)
if [ -n "$JOB_ID_1" ] && [ "$JOB_ID_1" = "$JOB_ID_2" ]; then
  ok "Idempotent submission returns one queued Job"
else
  bad "Idempotent submission failed: first=$JOB1 second=$JOB2"
fi

LOOKUP=$(curl -fsS --max-time 5 "$AUTOMATION_URL/v1/jobs/$JOB_ID_1" 2>/dev/null || true)
LOOKUP_STATUS=$(printf '%s' "$LOOKUP" | jget "['status']" 2>/dev/null || true)
if [ "$LOOKUP_STATUS" = "queued" ]; then
  ok "Queued Job is queryable"
else
  bad "Job lookup failed: $LOOKUP"
fi

echo
echo "$PASS passed, $FAIL failed"
if [ "$FAIL" -gt 0 ]; then
  exit 1
fi
