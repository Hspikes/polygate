#!/usr/bin/env bash
# Prove the Policy deployment regression suite never depends on a live
# Kubernetes API server or kubectl discovery.

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
POLICY_TEST="$ROOT_DIR/scripts/tests/test-deployment-policy.sh"
TEST_DIR="$(mktemp -d "${TMPDIR:-/tmp}/polygate-policy-offline-test.XXXXXX")"
FAKE_BIN="$TEST_DIR/bin"
OFFLINE_KUBECTL_LOG="$TEST_DIR/kubectl.log"
EMPTY_KUBECONFIG="$TEST_DIR/kubeconfig"
TEST_OUTPUT="$TEST_DIR/test.out"

cleanup() {
  rm -rf "$TEST_DIR"
}
trap cleanup EXIT

mkdir -p "$FAKE_BIN"
: >"$EMPTY_KUBECONFIG"
: >"$OFFLINE_KUBECTL_LOG"

cat >"$FAKE_BIN/kubectl" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail

printf '%s\n' "$*" >>"$OFFLINE_KUBECTL_LOG"
echo "offline guard: external kubectl must not be called" >&2
exit 97
EOF
chmod +x "$FAKE_BIN/kubectl"

set +e
PATH="$FAKE_BIN:$PATH" \
  KUBECONFIG="$EMPTY_KUBECONFIG" \
  OFFLINE_KUBECTL_LOG="$OFFLINE_KUBECTL_LOG" \
  bash "$POLICY_TEST" >"$TEST_OUTPUT" 2>&1
exit_code=$?
set -e

if [ "$exit_code" -ne 0 ]; then
  cat "$TEST_OUTPUT" >&2
  echo "FAIL: Policy deployment checks are not fully offline" >&2
  exit 1
fi

if [ -s "$OFFLINE_KUBECTL_LOG" ]; then
  echo "Unexpected external kubectl calls:" >&2
  cat "$OFFLINE_KUBECTL_LOG" >&2
  exit 1
fi

echo "Policy deployment checks run with an empty kubeconfig and no API server."
