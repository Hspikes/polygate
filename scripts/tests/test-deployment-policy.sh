#!/usr/bin/env bash
# Regression checks for the private Policy control-plane Kubernetes wiring.
# Run with:
#   bash scripts/tests/test-deployment-policy.sh

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
RBAC_MANIFEST="$ROOT_DIR/deploy/policy-rbac.yaml"
DEPLOY_SCRIPT="$ROOT_DIR/scripts/deploy-kubernetes-application.sh"
RENDER_SCRIPT="$ROOT_DIR/scripts/render-default-policy-store.py"
MANIFEST_TEST="$ROOT_DIR/scripts/tests/test-deployment-policy-manifests.py"
COMPOSE_FILE="$ROOT_DIR/docker-compose.yml"
TEST_DIR="$(mktemp -d "${TMPDIR:-/tmp}/polygate-policy-deploy-test.XXXXXX")"
FAKE_BIN="$TEST_DIR/bin"
KUBECTL_LOG="$TEST_DIR/kubectl.log"

cleanup() {
  rm -rf "$TEST_DIR"
}
trap cleanup EXIT

fail() {
  echo "FAIL: $1" >&2
  exit 1
}

require_file() {
  if [ ! -f "$1" ]; then
    fail "missing required file: ${1#"$ROOT_DIR/"}"
  fi
}

require_text() {
  local file="$1"
  local expected="$2"
  if ! grep -Fq -- "$expected" "$file"; then
    fail "${file#"$ROOT_DIR/"} is missing: $expected"
  fi
}

reject_text() {
  local file="$1"
  local rejected="$2"
  if grep -Fq -- "$rejected" "$file"; then
    fail "${file#"$ROOT_DIR/"} contains forbidden text: $rejected"
  fi
}

require_file "$RBAC_MANIFEST"
require_file "$RENDER_SCRIPT"
require_file "$MANIFEST_TEST"

python3 "$MANIFEST_TEST"

require_text "$DEPLOY_SCRIPT" \
  'kubectl get secret "$POLICY_ADMIN_SECRET"'
require_text "$DEPLOY_SCRIPT" \
  'kubectl get configmap "$POLICY_CONFIGMAP"'
require_text "$DEPLOY_SCRIPT" \
  'python3 "$ROOT_DIR/scripts/render-default-policy-store.py"'
require_text "$DEPLOY_SCRIPT" \
  'kubectl create configmap "$POLICY_CONFIGMAP"'
require_text "$DEPLOY_SCRIPT" \
  "Preserving existing polygate-routing-policy ConfigMap and version history"
reject_text "$DEPLOY_SCRIPT" \
  'kubectl apply --filename "$ROOT_DIR/deploy/default-policy'

for expected in \
  'POLICY_FILE: /config/policy-store.json' \
  'POLICY_API_URL: http://automation:8020' \
  'POLICY_REFRESH_SECONDS: "5"' \
  'POLICY_ADMIN_KEY: local-policy-admin-development' \
  'POLICY_ALLOW_ENV_ADMIN_KEY: "true"' \
  'ports: ["127.0.0.1:8020:8020"]' \
  'policy-store:/config:ro'; do
  require_text "$COMPOSE_FILE" "$expected"
done

python3 "$RENDER_SCRIPT" "$ROOT_DIR/contracts/policy-examples.json" \
  | python3 -c '
import json
import sys

document = json.load(sys.stdin)
assert set(document) == {"active_version", "versions"}
assert document["active_version"] >= 1
assert len(document["versions"]) >= 1
assert any(
    item["version"] == document["active_version"]
    and item["status"] == "active"
    for item in document["versions"]
)
'

mkdir -p "$FAKE_BIN"
cat >"$FAKE_BIN/kubectl" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail

printf '%s\n' "$*" >>"$KUBECTL_LOG"

if [ "${1:-}" = "get" ] && [ "${2:-}" = "secret" ]; then
  case "${3:-}" in
    gateway-client-secrets)
      printf '%s' $'api-keys\nweb-api-key\nworker-api-key\n'
      ;;
    polygate-policy-admin)
      if [[ "$*" == *'{{len (index .data "admin-key")}}'* ]]; then
        printf '%s' "32"
      else
        printf '%s' $'admin-key\n'
      fi
      ;;
  esac
  exit 0
fi

if [ "${1:-}" = "config" ] && [ "${2:-}" = "current-context" ]; then
  printf '%s\n' "policy-test-context"
  exit 0
fi

if [ "${1:-}" = "get" ] && [ "${2:-}" = "--raw" ]; then
  exit 0
fi

if [ "${1:-}" = "get" ] && [ "${2:-}" = "configmap" ]; then
  [ "${MOCK_POLICY_CONFIGMAP_EXISTS:-0}" = "1" ]
  exit
fi

exit 0
EOF
chmod +x "$FAKE_BIN/kubectl"

run_fake_deploy() {
  local configmap_exists="$1"
  local output_file="$2"
  : >"$KUBECTL_LOG"
  PATH="$FAKE_BIN:$PATH" \
    KUBECTL_LOG="$KUBECTL_LOG" \
    MOCK_POLICY_CONFIGMAP_EXISTS="$configmap_exists" \
    IMAGE_TAG="policy-deploy-test" \
    INCLUDE_AUTOMATION=1 \
    "$DEPLOY_SCRIPT" >"$output_file" 2>&1
}

CREATED_OUTPUT="$TEST_DIR/created.out"
run_fake_deploy 0 "$CREATED_OUTPUT"
require_text "$CREATED_OUTPUT" \
  "Created initial polygate-routing-policy ConfigMap"
if [ "$(grep -Fc 'create configmap polygate-routing-policy ' "$KUBECTL_LOG")" -ne 1 ]; then
  fail "missing ConfigMap must be created exactly once"
fi

PRESERVED_OUTPUT="$TEST_DIR/preserved.out"
run_fake_deploy 1 "$PRESERVED_OUTPUT"
require_text "$PRESERVED_OUTPUT" \
  "Preserving existing polygate-routing-policy ConfigMap and version history"
if grep -Fq 'create configmap polygate-routing-policy ' "$KUBECTL_LOG"; then
  fail "existing Policy ConfigMap must never be recreated or overwritten"
fi

echo "Policy deployment wiring preserves history and least privilege."
