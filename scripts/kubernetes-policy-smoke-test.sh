#!/usr/bin/env bash
# Verify the policy lifecycle end to end against a deployed cluster:
# read -> auth -> validate -> preview -> publish -> converge -> rollback -> converge.
#
# Reach the private services through port-forwards before running:
#   kubectl port-forward deployment/automation 8020:8020
#   kubectl port-forward deployment/prometheus 9090:9090
#
# The admin key is read from POLICY_ADMIN_KEY and is never printed. Do not run
# this script under `set -x`; that would echo the Authorization header.
#
# If the script exits after publishing but before rolling back, an EXIT trap
# restores the original policy content so the cluster is never left on a
# throwaway version.

set -uo pipefail

AUTOMATION_URL="${AUTOMATION_URL:-http://localhost:8020}"
PROMETHEUS_URL="${PROMETHEUS_URL:-http://localhost:9090}"
CONVERGE_TIMEOUT_SECONDS="${CONVERGE_TIMEOUT_SECONDS:-60}"
# Provider side-effect check compares a Prometheus counter across a preview.
# It must outlast one scrape interval, otherwise the "after" sample can predate
# the preview and the assertion would pass without proving anything.
SCRAPE_SETTLE_SECONDS="${SCRAPE_SETTLE_SECONDS:-20}"

if [ -z "${POLICY_ADMIN_KEY:-}" ]; then
  echo "Set POLICY_ADMIN_KEY (it is never printed by this script)." >&2
  exit 1
fi

PASS=0
FAIL=0
ORIGINAL_VERSION=""
ORIGINAL_POLICY_FILE=""
PUBLISHED=0
RESTORED=0

ok() {
  PASS=$((PASS + 1))
  echo "  OK  $1"
}

bad() {
  FAIL=$((FAIL + 1))
  echo "  FAIL  $1"
}

WORK_DIR="$(mktemp -d)"

cleanup() {
  local exit_code=$?
  if [ "$PUBLISHED" = "1" ] && [ "$RESTORED" != "1" ]; then
    echo
    echo "  !!  exiting with an unrestored publish; rolling back to v$ORIGINAL_VERSION"
    local current
    current="$(active_version 2>/dev/null || echo "")"
    if [ -n "$current" ]; then
      admin_post "/v1/admin/policies/$ORIGINAL_VERSION/rollback" \
        "{\"base_version\": $current, \"change_note\": \"policy smoke trap restore\"}" \
        >/dev/null 2>&1 \
        && echo "  !!  rollback issued" \
        || echo "  !!  rollback FAILED; inspect the cluster policy state manually" >&2
    fi
  fi
  rm -rf "$WORK_DIR"
  exit "$exit_code"
}

# ---------- HTTP helpers ----------

# Prints the response body; returns non-zero on a non-2xx status.
admin_get() {
  curl -fsS -H "Authorization: Bearer $POLICY_ADMIN_KEY" "$AUTOMATION_URL$1"
}

admin_post() {
  curl -fsS -X POST \
    -H "Authorization: Bearer $POLICY_ADMIN_KEY" \
    -H "Content-Type: application/json" \
    --data-binary "$2" \
    "$AUTOMATION_URL$1"
}

# Prints only the HTTP status code. Used where the body is irrelevant and must
# not be echoed.
status_code() {
  local method="$1" path="$2" key="${3:-}" body="${4:-}"
  local args=(-sS -o /dev/null -w '%{http_code}' -X "$method")
  [ -n "$key" ] && args+=(-H "Authorization: Bearer $key")
  if [ -n "$body" ]; then
    args+=(-H "Content-Type: application/json" --data-binary "$body")
  fi
  curl "${args[@]}" "$AUTOMATION_URL$path"
}

active_version() {
  curl -fsS "$AUTOMATION_URL/v1/policies/active" \
    | python3 -c 'import json,sys; print(json.load(sys.stdin)["version"])'
}

promql() {
  curl -fsS --get \
    --data-urlencode "query=$1" \
    "$PROMETHEUS_URL/api/v1/query" \
    | python3 -c '
import json
import sys

payload = json.load(sys.stdin)
results = payload.get("data", {}).get("result", [])
if payload.get("status") != "success":
    raise SystemExit(1)
# An absent series is a legitimate zero for counter comparisons.
print(results[0]["value"][1] if results else "0")
'
}

# Waits until every component reports the expected version, or times out.
converge() {
  local expected="$1" deadline=$((SECONDS + CONVERGE_TIMEOUT_SECONDS))
  local gateway_targets gateway_loaded worker_loaded active
  while [ "$SECONDS" -lt "$deadline" ]; do
    gateway_targets="$(promql 'count(up{job="polygate-gateway"})' 2>/dev/null || echo 0)"
    gateway_loaded="$(
      promql "count(polygate_policy_loaded_version{component=\"gateway\"} == $expected)" \
        2>/dev/null || echo 0
    )"
    worker_loaded="$(
      promql "count(polygate_policy_loaded_version{component=\"automation-worker\"} == $expected)" \
        2>/dev/null || echo 0
    )"
    active="$(promql 'max(polygate_policy_active_version)' 2>/dev/null || echo 0)"
    if python3 -c "
raise SystemExit(0 if (
    float('$active') == $expected
    and float('$gateway_targets') >= 1
    and float('$gateway_loaded') == float('$gateway_targets')
    and float('$worker_loaded') == 1
) else 1)
"; then
      echo "$gateway_loaded/$gateway_targets gateway, worker ok"
      return 0
    fi
    sleep 2
  done
  echo "timed out (active=$active gateway=$gateway_loaded/$gateway_targets worker=$worker_loaded)"
  return 1
}

# INT/TERM are included on purpose: an operator aborting with Ctrl-C after the
# publish is the realistic way to strand the cluster on a throwaway version.
trap cleanup EXIT INT TERM

echo "PolyGate policy lifecycle smoke test"
echo "Automation: $AUTOMATION_URL"
echo "Prometheus: $PROMETHEUS_URL"
echo

# ---------- 1. read the active policy ----------

ACTIVE_JSON="$WORK_DIR/active.json"
if curl -fsS "$AUTOMATION_URL/v1/policies/active" -o "$ACTIVE_JSON"; then
  ORIGINAL_VERSION="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["version"])' "$ACTIVE_JSON")"
  ok "Read active policy (v$ORIGINAL_VERSION)"
else
  bad "Cannot read GET /v1/policies/active"
  echo
  echo "$PASS passed, $FAIL failed"
  exit 1
fi

ORIGINAL_POLICY_FILE="$WORK_DIR/original-policy.json"
python3 -c '
import json
import sys

active = json.load(open(sys.argv[1], encoding="utf-8"))
json.dump(active["policy"], open(sys.argv[2], "w", encoding="utf-8"))
' "$ACTIVE_JSON" "$ORIGINAL_POLICY_FILE"

# The public endpoint must not leak audit fields.
if python3 -c '
import json
import sys

active = json.load(open(sys.argv[1], encoding="utf-8"))
leaked = {"created_by", "change_note", "rollback_from"} & set(active)
raise SystemExit(1 if leaked else 0)
' "$ACTIVE_JSON"; then
  ok "Active response exposes no audit fields"
else
  bad "Active response leaks created_by/change_note/rollback_from"
fi

# ---------- 2. authentication ----------

NO_KEY_STATUS="$(status_code GET /v1/admin/policies)"
if [ "$NO_KEY_STATUS" = "401" ]; then
  ok "Admin API without a key returns 401"
else
  bad "Admin API without a key returned $NO_KEY_STATUS (expected 401)"
fi

BAD_KEY_STATUS="$(status_code GET /v1/admin/policies "smoke-invalid-key-not-the-real-one")"
if [ "$BAD_KEY_STATUS" = "401" ]; then
  ok "Admin API with an invalid key returns 401"
else
  bad "Admin API with an invalid key returned $BAD_KEY_STATUS (expected 401)"
fi

if admin_get /v1/admin/policies >/dev/null 2>&1; then
  ok "Admin API accepts the configured key"
else
  bad "Admin API rejected the configured key"
fi

# ---------- 3. validate a changed high_quality_strategy ----------

CHANGED_POLICY_FILE="$WORK_DIR/changed-policy.json"
python3 -c '
import json
import sys

policy = json.load(open(sys.argv[1], encoding="utf-8"))
current = policy["gateway"]["high_quality_strategy"]
policy["gateway"]["high_quality_strategy"] = (
    "lowest_cost" if current == "prefer_real" else "prefer_real"
)
json.dump(policy, open(sys.argv[2], "w", encoding="utf-8"))
print(policy["gateway"]["high_quality_strategy"])
' "$ORIGINAL_POLICY_FILE" "$CHANGED_POLICY_FILE" > "$WORK_DIR/new-strategy.txt"
NEW_STRATEGY="$(cat "$WORK_DIR/new-strategy.txt")"

if admin_post /v1/admin/policies/validate "$(cat "$CHANGED_POLICY_FILE")" \
  | python3 -c 'import json,sys; raise SystemExit(0 if json.load(sys.stdin).get("valid") else 1)'; then
  ok "Validate accepts high_quality_strategy=$NEW_STRATEGY"
else
  bad "Validate rejected the changed draft"
fi

# ---------- 4. preview must not call Providers ----------

PROVIDER_BEFORE="$(promql 'sum(polygate_provider_requests_total)' 2>/dev/null || echo 0)"
PREVIEW_BODY="$WORK_DIR/preview.json"
python3 -c '
import json
import sys

policy = json.load(open(sys.argv[1], encoding="utf-8"))
request = {
    "policy": policy,
    "gateway_cases": [
        {
            "model": "auto",
            "messages": [{"role": "user", "content": "policy smoke routing case"}],
            "polygate": {
                "quality": "high",
                "privacy": "standard",
                "max_cost_usd": 0.01,
                "latency_target_ms": 1000,
            },
        }
    ],
    "priority_cases": [
        {
            "employee": "policy-smoke",
            "department": "engineering",
            "scenario": "production_incident",
            "urgency": "critical",
            "prompt": "policy smoke priority case",
            "preferences": {
                "quality": "high",
                "privacy": "standard",
                "max_cost_usd": 0.01,
                "latency_target_ms": 1000,
            },
        }
    ],
}
json.dump(request, open(sys.argv[2], "w", encoding="utf-8"))
' "$CHANGED_POLICY_FILE" "$PREVIEW_BODY"

if admin_post /v1/admin/policies/preview "$(cat "$PREVIEW_BODY")" \
  > "$WORK_DIR/preview-response.json" 2>/dev/null; then
  if python3 -c '
import json
import sys

body = json.load(open(sys.argv[1], encoding="utf-8"))
simulations = body.get("simulations", {})
routing = simulations.get("routing", [])
case_ids = [entry.get("case_id") for entry in routing]
raise SystemExit(
    0
    if body.get("diff")
    and routing
    and len(case_ids) == len(set(case_ids))
    else 1
)
' "$WORK_DIR/preview-response.json"; then
    ok "Preview returns a diff and uniquely identified routing simulations"
  else
    bad "Preview response is missing a diff or has duplicate case_id values"
  fi
else
  bad "Preview request failed"
fi

sleep "$SCRAPE_SETTLE_SECONDS"
PROVIDER_AFTER="$(promql 'sum(polygate_provider_requests_total)' 2>/dev/null || echo 0)"
if python3 -c "raise SystemExit(0 if float('$PROVIDER_AFTER') == float('$PROVIDER_BEFORE') else 1)"; then
  ok "Preview caused no Provider requests ($PROVIDER_BEFORE unchanged)"
else
  bad "Provider counter moved during preview ($PROVIDER_BEFORE -> $PROVIDER_AFTER)"
fi

# ---------- 5. publish ----------

PUBLISH_BODY="$(
  python3 -c '
import json
import sys

policy = json.load(open(sys.argv[1], encoding="utf-8"))
print(json.dumps({
    "base_version": int(sys.argv[2]),
    "change_note": "policy smoke publish",
    "policy": policy,
}))
' "$CHANGED_POLICY_FILE" "$ORIGINAL_VERSION"
)"

if PUBLISH_RESPONSE="$(admin_post /v1/admin/policies/publish "$PUBLISH_BODY" 2>/dev/null)"; then
  PUBLISHED=1
  PUBLISHED_VERSION="$(echo "$PUBLISH_RESPONSE" | python3 -c 'import json,sys; print(json.load(sys.stdin)["version"])')"
  if [ "$PUBLISHED_VERSION" = "$((ORIGINAL_VERSION + 1))" ]; then
    ok "Published v$PUBLISHED_VERSION"
  else
    bad "Publish returned v$PUBLISHED_VERSION (expected v$((ORIGINAL_VERSION + 1)))"
  fi
else
  bad "Publish failed"
  echo
  echo "$PASS passed, $FAIL failed"
  exit 1
fi

STALE_STATUS="$(
  status_code POST /v1/admin/policies/publish "$POLICY_ADMIN_KEY" "$PUBLISH_BODY"
)"
if [ "$STALE_STATUS" = "409" ]; then
  ok "Re-publishing a stale base_version returns 409"
else
  bad "Stale publish returned $STALE_STATUS (expected 409)"
fi

# ---------- 6. all components load the new version ----------

if CONVERGE_RESULT="$(converge "$PUBLISHED_VERSION")"; then
  ok "Components converged on v$PUBLISHED_VERSION ($CONVERGE_RESULT)"
else
  bad "Components did not converge on v$PUBLISHED_VERSION: $CONVERGE_RESULT"
fi

# ---------- 7. rollback to the original content ----------

ROLLBACK_BODY="{\"base_version\": $PUBLISHED_VERSION, \"change_note\": \"policy smoke rollback\"}"
if ROLLBACK_RESPONSE="$(
  admin_post "/v1/admin/policies/$ORIGINAL_VERSION/rollback" "$ROLLBACK_BODY" 2>/dev/null
)"; then
  ROLLED_BACK_VERSION="$(echo "$ROLLBACK_RESPONSE" | python3 -c 'import json,sys; print(json.load(sys.stdin)["version"])')"
  if [ "$ROLLED_BACK_VERSION" = "$((PUBLISHED_VERSION + 1))" ]; then
    ok "Rollback created v$ROLLED_BACK_VERSION from v$ORIGINAL_VERSION"
  else
    bad "Rollback returned v$ROLLED_BACK_VERSION (expected v$((PUBLISHED_VERSION + 1)))"
  fi
  RESTORED=1
else
  bad "Rollback failed"
  ROLLED_BACK_VERSION=""
fi

if [ -n "$ROLLED_BACK_VERSION" ]; then
  if curl -fsS "$AUTOMATION_URL/v1/policies/active" \
    | python3 -c '
import json
import sys

expected = json.load(open(sys.argv[1], encoding="utf-8"))
raise SystemExit(0 if json.load(sys.stdin)["policy"] == expected else 1)
' "$ORIGINAL_POLICY_FILE"; then
    ok "Rollback restored the original policy content"
  else
    bad "Active policy content differs from the original after rollback"
  fi

  # ---------- 8. converge again ----------

  if CONVERGE_RESULT="$(converge "$ROLLED_BACK_VERSION")"; then
    ok "Components converged on v$ROLLED_BACK_VERSION ($CONVERGE_RESULT)"
  else
    bad "Components did not converge on v$ROLLED_BACK_VERSION: $CONVERGE_RESULT"
  fi
fi

# ---------- 9. the admin key must never appear in exported metrics ----------

if curl -fsS "$AUTOMATION_URL/metrics" | grep -qF "$POLICY_ADMIN_KEY"; then
  bad "The admin key appears in the Automation /metrics output"
else
  ok "The admin key does not appear in exported metrics"
fi

echo
echo "$PASS passed, $FAIL failed"

if [ "$FAIL" -gt 0 ]; then
  exit 1
fi
