#!/usr/bin/env bash
# ============================================================
# PolyGate P0 smoke test
# ------------------------------------------------------------
# Run this after `docker compose up` (or after pulling someone else's
# changes) to check the whole local stack is actually working:
#   health -> routing + decision card -> exact cache hit -> fault injection
#
# Usage:
#   ./scripts/smoke-test.sh
#   GATEWAY=http://localhost:8000 MOCK_B_ADMIN=http://localhost:8082/admin ./scripts/smoke-test.sh
#
# Exit code 0 = all checks passed, 1 = something is broken.
# Requires: curl, python3 (used only for JSON parsing, no extra deps).
# ============================================================
set -u

GATEWAY="${GATEWAY:-http://localhost:8000}"
MOCK_B_ADMIN="${MOCK_B_ADMIN:-http://localhost:8082/admin}"

PASS=0
FAIL=0
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; NC='\033[0m'

ok()   { PASS=$((PASS+1)); echo -e "  ${GREEN}✔${NC} $1"; }
bad()  { FAIL=$((FAIL+1)); echo -e "  ${RED}✘${NC} $1"; }
info() { echo -e "${YELLOW}$1${NC}"; }

jget() { python3 -c "import sys,json; d=json.load(sys.stdin); print(d$1)" 2>/dev/null; }

echo "PolyGate smoke test — gateway: $GATEWAY"
echo

# ---------- 1. health ----------
info "[1/4] Health check"
HEALTH=$(curl -s -m 5 "$GATEWAY/health")
if [ -z "$HEALTH" ]; then
  bad "gateway unreachable at $GATEWAY (is 'docker compose up' running?)"
  echo; echo "Aborting remaining checks — nothing else can pass without the gateway."
  exit 1
fi
STATUS=$(echo "$HEALTH" | jget "['status']")
CACHE_ENABLED=$(echo "$HEALTH" | jget "['cache']")
[ "$STATUS" = "ok" ] && ok "gateway healthy: $HEALTH" || bad "unexpected /health response: $HEALTH"
if [ "$CACHE_ENABLED" = "True" ]; then
  ok "redis cache connected"
else
  echo -e "  ${YELLOW}!${NC} redis not connected — cache will no-op (fine for early dev, not fine by P0 freeze)"
fi

# ---------- 2. routing + decision card ----------
info "[2/4] Routing + decision card"
NONCE=$(date +%s%N)
REQ_BODY="{\"model\":\"auto\",\"messages\":[{\"role\":\"user\",\"content\":\"smoke test $NONCE\"}],\"polygate\":{\"quality\":\"balanced\",\"max_cost_usd\":0.01}}"
RESP1=$(curl -s -m 10 -X POST "$GATEWAY/v1/chat/completions" -H 'Content-Type: application/json' -d "$REQ_BODY")
PROVIDER=$(echo "$RESP1" | jget "['polygate']['chosen_provider']")
REASON=$(echo "$RESP1" | jget "['polygate']['reason']")
REQID=$(echo "$RESP1" | jget "['polygate']['request_id']")
if [ -n "$PROVIDER" ] && [ -n "$REQID" ]; then
  ok "routed to '$PROVIDER' — reason: ${REASON:0:60}..."
  ok "request_id present: $REQID"
else
  bad "decision card missing expected fields. Raw response: $RESP1"
fi

# ---------- 3. exact cache hit on identical repeat ----------
info "[3/4] Exact cache hit on repeat request"
RESP2=$(curl -s -m 10 -X POST "$GATEWAY/v1/chat/completions" -H 'Content-Type: application/json' -d "$REQ_BODY")
CACHE_HIT=$(echo "$RESP2" | jget "['polygate']['cache_hit']")
COST2=$(echo "$RESP2" | jget "['polygate']['cost_estimate_usd']")
if [ "$CACHE_HIT" = "True" ] && [ "$COST2" = "0.0" ]; then
  ok "repeat request hit cache, cost \$0"
else
  bad "expected cache_hit=true, cost=0 on repeat request. Got: $RESP2"
fi

# ---------- 4. fault injection round-trip ----------
info "[4/4] Fault injection on mock-b (then auto-reset)"
CFG=$(curl -s -m 5 -X POST "$MOCK_B_ADMIN/config" -H 'Content-Type: application/json' -d '{"fail_rate": 1.0}')
if echo "$CFG" | grep -q '"ok":true' 2>/dev/null || echo "$CFG" | jget "['ok']" | grep -qi true; then
  ok "mock-b fail_rate set to 1.0"
else
  bad "could not reach mock-b admin endpoint at $MOCK_B_ADMIN (is it running / correct port?)"
fi

NONCE2=$(date +%s%N)
FAIL_BODY="{\"model\":\"auto\",\"messages\":[{\"role\":\"user\",\"content\":\"smoke test fault $NONCE2\"}],\"polygate\":{\"quality\":\"balanced\",\"max_cost_usd\":0.01}}"
HTTP_CODE=$(curl -s -m 10 -o /dev/null -w "%{http_code}" -X POST "$GATEWAY/v1/chat/completions" -H 'Content-Type: application/json' -d "$FAIL_BODY")
# P0 has no failover yet, so a provider error should surface as 502 or 503 (no eligible provider).
if [ "$HTTP_CODE" = "502" ] || [ "$HTTP_CODE" = "503" ]; then
  ok "fault correctly surfaced as HTTP $HTTP_CODE (expected until P1 adds failover)"
else
  bad "expected HTTP 502/503 while mock-b is failing, got $HTTP_CODE"
fi

curl -s -m 5 -X POST "$MOCK_B_ADMIN/reset" > /dev/null
ok "mock-b reset to normal"

# ---------- summary ----------
echo
echo "----------------------------------------"
if [ "$FAIL" -eq 0 ]; then
  echo -e "${GREEN}All $PASS checks passed.${NC}"
  exit 0
else
  echo -e "${RED}$FAIL check(s) failed${NC}, $PASS passed."
  exit 1
fi