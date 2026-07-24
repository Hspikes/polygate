#!/usr/bin/env bash
# Verify that application deployment validates required Secret keys before
# performing any Kubernetes mutation.

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
DEPLOY_SCRIPT="$ROOT_DIR/scripts/deploy-kubernetes-application.sh"
TEST_DIR="$(mktemp -d "${TMPDIR:-/tmp}/polygate-secret-test.XXXXXX")"
FAKE_BIN="$TEST_DIR/bin"
KUBECTL_LOG="$TEST_DIR/kubectl.log"

cleanup() {
  rm -rf "$TEST_DIR"
}
trap cleanup EXIT

mkdir -p "$FAKE_BIN"

cat >"$FAKE_BIN/kubectl" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail

printf '%s\n' "$*" >>"$KUBECTL_LOG"

if [ "${1:-}" = "get" ] && [ "${2:-}" = "secret" ]; then
  if [ "${MOCK_SECRET_EXISTS:-0}" != "1" ]; then
    exit 1
  fi
  printf '%s' "${MOCK_SECRET_KEYS:-}"
  exit 0
fi

if [ "${1:-}" = "config" ] && [ "${2:-}" = "current-context" ]; then
  printf '%s\n' "review-context"
  exit 0
fi

if [ "${1:-}" = "get" ] && [ "${2:-}" = "--raw" ]; then
  # Stop successful Secret cases before the first apply. The test only covers
  # the pre-mutation validation gate.
  exit 1
fi

exit 0
EOF
chmod +x "$FAKE_BIN/kubectl"

fail() {
  echo "FAIL: $1" >&2
  exit 1
}

assert_contains() {
  local text="$1"
  local expected="$2"
  if [[ "$text" != *"$expected"* ]]; then
    fail "expected output to contain: $expected"
  fi
}

assert_no_mutation() {
  if grep -Eq '(^| )(apply|create|delete|patch|replace|rollout|scale)( |$)' \
    "$KUBECTL_LOG"; then
    echo "Unexpected kubectl mutation calls:" >&2
    cat "$KUBECTL_LOG" >&2
    exit 1
  fi
}

run_deploy() {
  local include_automation="$1"
  local secret_exists="$2"
  local secret_keys="$3"
  local output_file="$4"

  : >"$KUBECTL_LOG"
  set +e
  PATH="$FAKE_BIN:$PATH" \
    KUBECTL_LOG="$KUBECTL_LOG" \
    MOCK_SECRET_EXISTS="$secret_exists" \
    MOCK_SECRET_KEYS="$secret_keys" \
    IMAGE_TAG="review-secret-gate" \
    INCLUDE_AUTOMATION="$include_automation" \
    "$DEPLOY_SCRIPT" >"$output_file" 2>&1
  local exit_code=$?
  set -e
  return "$exit_code"
}

MISSING_SECRET_OUTPUT="$TEST_DIR/missing-secret.out"
if run_deploy 0 0 "" "$MISSING_SECRET_OUTPUT"; then
  fail "deployment unexpectedly succeeded without gateway-client-secrets"
fi
assert_contains \
  "$(cat "$MISSING_SECRET_OUTPUT")" \
  "Missing Secret default/gateway-client-secrets"
assert_no_mutation

MISSING_WEB_KEY_OUTPUT="$TEST_DIR/missing-web-key.out"
if run_deploy 0 1 $'api-keys\n' "$MISSING_WEB_KEY_OUTPUT"; then
  fail "deployment unexpectedly succeeded without web-api-key"
fi
assert_contains "$(cat "$MISSING_WEB_KEY_OUTPUT")" "web-api-key"
assert_no_mutation

NO_AUTOMATION_OUTPUT="$TEST_DIR/no-automation.out"
if run_deploy 0 1 $'api-keys\nweb-api-key\n' "$NO_AUTOMATION_OUTPUT"; then
  fail "deployment unexpectedly passed the intentionally unavailable Metrics API"
fi
assert_contains \
  "$(cat "$NO_AUTOMATION_OUTPUT")" \
  "Kubernetes Metrics API is unavailable"
if grep -Fq "worker-api-key" "$NO_AUTOMATION_OUTPUT"; then
  fail "worker-api-key must not be required when INCLUDE_AUTOMATION=0"
fi
assert_no_mutation

MISSING_WORKER_KEY_OUTPUT="$TEST_DIR/missing-worker-key.out"
if run_deploy 1 1 $'api-keys\nweb-api-key\n' "$MISSING_WORKER_KEY_OUTPUT"; then
  fail "deployment unexpectedly succeeded without worker-api-key"
fi
assert_contains "$(cat "$MISSING_WORKER_KEY_OUTPUT")" "worker-api-key"
assert_no_mutation

ALL_KEYS_OUTPUT="$TEST_DIR/all-keys.out"
if run_deploy 1 1 $'api-keys\nweb-api-key\nworker-api-key\n' "$ALL_KEYS_OUTPUT"; then
  fail "deployment unexpectedly passed the intentionally unavailable Metrics API"
fi
assert_contains \
  "$(cat "$ALL_KEYS_OUTPUT")" \
  "Kubernetes Metrics API is unavailable"
assert_no_mutation

DEPLOY_README="$ROOT_DIR/deploy/README.md"
for expected in \
  'read -s WEB_GATEWAY_KEY' \
  'read -s WORKER_GATEWAY_KEY' \
  '--from-literal=api-keys="$WEB_GATEWAY_KEY,$WORKER_GATEWAY_KEY"' \
  '--from-literal=web-api-key="$WEB_GATEWAY_KEY"' \
  '--from-literal=worker-api-key="$WORKER_GATEWAY_KEY"' \
  'unset WEB_GATEWAY_KEY WORKER_GATEWAY_KEY'; do
  if ! grep -Fq -- "$expected" "$DEPLOY_README"; then
    fail "deploy/README.md is missing the complete Secret instruction: $expected"
  fi
done

echo "Deployment Secret validation fails safely before Kubernetes mutations."
