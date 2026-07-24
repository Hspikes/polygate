#!/usr/bin/env bash
# Verify local Gateway -> Prometheus collection.
#
# Run after:
#   docker compose up --build -d

set -uo pipefail

GATEWAY="${GATEWAY:-http://localhost:8000}"
PROMETHEUS="${PROMETHEUS:-http://localhost:9090}"

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

query() {
  curl -fsS --get "$PROMETHEUS/api/v1/query" --data-urlencode "query=$1"
}

query_value() {
  python3 -c '
import json
import sys

payload = json.load(sys.stdin)
results = payload.get("data", {}).get("result", [])
print(results[0]["value"][1] if results else "0")
'
}

send_gateway_request() {
  local nonce
  local request_body
  nonce="$(date +%s%N)"
  request_body="{\"model\":\"auto\",\"messages\":[{\"role\":\"user\",\"content\":\"prometheus smoke test $nonce\"}],\"polygate\":{\"quality\":\"balanced\",\"privacy\":\"standard\",\"max_cost_usd\":0.01,\"latency_target_ms\":3000}}"
  curl -sS -o /dev/null -w "%{http_code}" \
    -X POST "$GATEWAY/v1/chat/completions" \
    -H "Content-Type: application/json" \
    -d "$request_body"
}

echo "PolyGate Prometheus smoke test"
echo "Gateway:    $GATEWAY"
echo "Prometheus: $PROMETHEUS"

READY=false
for _ in $(seq 1 15); do
  if curl -fsS "$PROMETHEUS/-/ready" >/dev/null 2>&1; then
    READY=true
    break
  fi
  sleep 2
done

if [ "$READY" = true ]; then
  ok "Prometheus is ready"
else
  bad "Prometheus did not become ready within 30 seconds"
fi

if curl -fsS "$GATEWAY/metrics" | grep -q "polygate_requests_total"; then
  ok "Gateway exposes PolyGate metrics"
else
  bad "Gateway metrics endpoint is unavailable or missing PolyGate metrics"
fi

if curl -fsS "$GATEWAY/metrics" | grep -q "polygate_circuit_state"; then
  ok "Gateway exposes low-cardinality circuit state metrics"
else
  bad "Gateway circuit state metrics are missing"
fi

UP_VALUE=0
for _ in $(seq 1 15); do
  UP_VALUE="$(query 'up{job="polygate-gateway"}' 2>/dev/null | query_value 2>/dev/null || echo 0)"
  if [ "$UP_VALUE" = "1" ]; then
    break
  fi
  sleep 2
done

if [ "$UP_VALUE" = "1" ]; then
  ok "Prometheus reports the Gateway target as UP"
else
  bad "Prometheus Gateway target did not become UP within 30 seconds (value: $UP_VALUE)"
fi

# A container restart resets the in-process counter while Prometheus retains old
# samples. Send one warm-up request and wait for a scrape so the comparison below
# always uses the current Gateway process as its baseline.
WARMUP_HTTP_CODE="$(send_gateway_request)"
if [ "$WARMUP_HTTP_CODE" = "200" ]; then
  ok "Gateway accepted the warm-up request"
else
  bad "Gateway warm-up request returned HTTP $WARMUP_HTTP_CODE"
fi
sleep 7

BEFORE="$(query 'sum(polygate_requests_total)' 2>/dev/null | query_value 2>/dev/null || echo 0)"
HTTP_CODE="$(send_gateway_request)"

if [ "$HTTP_CODE" = "200" ]; then
  ok "Gateway accepted the measurement request"
else
  bad "Gateway measurement request returned HTTP $HTTP_CODE"
fi

# The scrape interval is 5 seconds; allow one full interval plus margin.
sleep 7

AFTER="$(query 'sum(polygate_requests_total)' 2>/dev/null | query_value 2>/dev/null || echo 0)"
if python3 -c "raise SystemExit(0 if float('$AFTER') > float('$BEFORE') else 1)"; then
  ok "Prometheus observed the request counter increase ($BEFORE -> $AFTER)"
else
  bad "Prometheus request counter did not increase ($BEFORE -> $AFTER)"
fi

echo
echo "$PASS passed, $FAIL failed"

if [ "$FAIL" -gt 0 ]; then
  exit 1
fi
