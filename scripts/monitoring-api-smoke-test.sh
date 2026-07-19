#!/usr/bin/env bash
# Verify local Prometheus -> Monitoring API aggregation.
#
# Run after:
#   docker compose up --build -d

set -uo pipefail

GATEWAY="${GATEWAY:-http://localhost:8000}"
MONITORING_API="${MONITORING_API:-http://localhost:8010}"

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

send_gateway_request() {
  local nonce
  local request_body
  nonce="$(date +%s%N)"
  request_body="{\"model\":\"auto\",\"messages\":[{\"role\":\"user\",\"content\":\"monitoring api smoke test $nonce\"}],\"polygate\":{\"quality\":\"balanced\",\"privacy\":\"standard\",\"max_cost_usd\":0.01,\"latency_target_ms\":3000}}"
  curl -sS -o /dev/null -w "%{http_code}" \
    -X POST "$GATEWAY/v1/chat/completions" \
    -H "Content-Type: application/json" \
    -d "$request_body"
}

overview_value() {
  local field_path
  field_path="$1"
  python3 -c "
import json
import sys

value = json.load(sys.stdin)
for part in '$field_path'.split('.'):
    value = value[part]
print(value)
"
}

echo "PolyGate Monitoring API smoke test"
echo "Gateway:        $GATEWAY"
echo "Monitoring API: $MONITORING_API"

READY=false
for _ in $(seq 1 20); do
  if curl -fsS "$MONITORING_API/health" >/dev/null 2>&1; then
    READY=true
    break
  fi
  sleep 2
done

if [ "$READY" = true ]; then
  ok "Monitoring API and Prometheus dependency are ready"
else
  bad "Monitoring API did not become ready within 40 seconds"
  echo
  echo "$PASS passed, $FAIL failed"
  exit 1
fi

OVERVIEW="$(curl -fsS "$MONITORING_API/api/monitoring/overview?window=15m" 2>/dev/null || echo '{}')"
if echo "$OVERVIEW" | python3 -c '
import json
import sys

body = json.load(sys.stdin)
required = {"generated_at", "window", "gateway", "cache", "usage", "providers", "resources"}
raise SystemExit(0 if required.issubset(body) else 1)
'; then
  ok "Overview response contains the stable top-level contract"
else
  bad "Overview response is missing required fields"
fi

WARMUP_HTTP_CODE="$(send_gateway_request)"
if [ "$WARMUP_HTTP_CODE" = "200" ]; then
  ok "Gateway accepted the warm-up request"
else
  bad "Gateway warm-up request returned HTTP $WARMUP_HTTP_CODE"
fi
sleep 7

BEFORE_JSON="$(curl -fsS "$MONITORING_API/api/monitoring/overview?window=15m" 2>/dev/null || echo '{}')"
BEFORE="$(echo "$BEFORE_JSON" | overview_value gateway.requests_total 2>/dev/null || echo 0)"

HTTP_CODE="$(send_gateway_request)"
if [ "$HTTP_CODE" = "200" ]; then
  ok "Gateway accepted the measurement request"
else
  bad "Gateway measurement request returned HTTP $HTTP_CODE"
fi
sleep 7

AFTER_JSON="$(curl -fsS "$MONITORING_API/api/monitoring/overview?window=15m" 2>/dev/null || echo '{}')"
AFTER="$(echo "$AFTER_JSON" | overview_value gateway.requests_total 2>/dev/null || echo 0)"

if python3 -c "raise SystemExit(0 if int('$AFTER') > int('$BEFORE') else 1)"; then
  ok "Monitoring API observed request growth ($BEFORE -> $AFTER)"
else
  bad "Monitoring API did not observe request growth ($BEFORE -> $AFTER)"
fi

RESOURCES_AVAILABLE="$(echo "$AFTER_JSON" | overview_value resources.available 2>/dev/null || echo missing)"
if [ "$RESOURCES_AVAILABLE" = "False" ]; then
  ok "Local response explicitly marks Kubernetes resources unavailable"
else
  bad "Unexpected local resources.available value: $RESOURCES_AVAILABLE"
fi

echo
echo "$PASS passed, $FAIL failed"

if [ "$FAIL" -gt 0 ]; then
  exit 1
fi
