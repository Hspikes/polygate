#!/usr/bin/env bash
# Regression checks for the private Policy control-plane Kubernetes wiring.
# Run with:
#   bash scripts/tests/test-deployment-policy.sh

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
RBAC_MANIFEST="$ROOT_DIR/deploy/policy-rbac.yaml"
AUTOMATION_MANIFEST="$ROOT_DIR/deploy/automation.yaml"
GATEWAY_MANIFEST="$ROOT_DIR/deploy/gateway.yaml"
DEPLOY_SCRIPT="$ROOT_DIR/scripts/deploy-kubernetes-application.sh"
RENDER_SCRIPT="$ROOT_DIR/scripts/render-default-policy-store.py"
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

resource_doc() {
  local file="$1"
  local resource_kind="$2"
  local resource_name="$3"

  awk \
    -v resource_kind="$resource_kind" \
    -v resource_name="$resource_name" \
    'BEGIN { RS="---" }
     $0 ~ "(^|\\n)kind: " resource_kind "(\\n|$)" &&
     ($0 ~ "(^|\\n)metadata:\\n  name: " resource_name "(\\n|$)" ||
      $0 ~ "(^|\\n)metadata: \\{ name: " resource_name " \\}(\\n|$)") {
       print
       found=1
     }
     END { if (!found) exit 1 }' \
    "$file"
}

require_resource_text() {
  local file="$1"
  local resource_kind="$2"
  local resource_name="$3"
  local expected="$4"
  local document

  if ! document="$(resource_doc "$file" "$resource_kind" "$resource_name")"; then
    fail "missing $resource_kind/$resource_name in ${file#"$ROOT_DIR/"}"
  fi
  if ! grep -Fq -- "$expected" <<<"$document"; then
    fail "$resource_kind/$resource_name is missing: $expected"
  fi
}

reject_resource_text() {
  local file="$1"
  local resource_kind="$2"
  local resource_name="$3"
  local rejected="$4"
  local document

  if ! document="$(resource_doc "$file" "$resource_kind" "$resource_name")"; then
    fail "missing $resource_kind/$resource_name in ${file#"$ROOT_DIR/"}"
  fi
  if grep -Fq -- "$rejected" <<<"$document"; then
    fail "$resource_kind/$resource_name contains forbidden text: $rejected"
  fi
}

reject_resource() {
  local file="$1"
  local resource_kind="$2"
  local resource_name="$3"
  if resource_doc "$file" "$resource_kind" "$resource_name" >/dev/null 2>&1; then
    fail "unexpected $resource_kind/$resource_name in ${file#"$ROOT_DIR/"}"
  fi
}

require_file "$RBAC_MANIFEST"
require_file "$RENDER_SCRIPT"

require_resource_text \
  "$RBAC_MANIFEST" ServiceAccount polygate-policy-controller \
  "name: polygate-policy-controller"
require_resource_text \
  "$RBAC_MANIFEST" Role polygate-policy-controller \
  'resources: ["configmaps"]'
require_resource_text \
  "$RBAC_MANIFEST" Role polygate-policy-controller \
  'resourceNames: ["polygate-routing-policy"]'
require_resource_text \
  "$RBAC_MANIFEST" Role polygate-policy-controller \
  'verbs: ["get", "update"]'
require_resource_text \
  "$RBAC_MANIFEST" RoleBinding polygate-policy-controller \
  "name: polygate-policy-controller"

for forbidden in \
  'resources: ["secrets"]' \
  'resources: ["pods"]' \
  'resources: ["deployments"]' \
  '"list"' \
  '"watch"' \
  '"create"' \
  '"patch"' \
  '"delete"'; do
  reject_resource_text \
    "$RBAC_MANIFEST" Role polygate-policy-controller "$forbidden"
done

require_resource_text \
  "$AUTOMATION_MANIFEST" Deployment automation \
  "serviceAccountName: polygate-policy-controller"
reject_resource_text \
  "$AUTOMATION_MANIFEST" Deployment automation \
  "automountServiceAccountToken: false"
require_resource_text \
  "$AUTOMATION_MANIFEST" Deployment automation-worker \
  "automountServiceAccountToken: false"
reject_resource_text \
  "$AUTOMATION_MANIFEST" Deployment automation-worker \
  "serviceAccountName: polygate-policy-controller"
reject_resource_text \
  "$GATEWAY_MANIFEST" Deployment gateway \
  "serviceAccountName: polygate-policy-controller"

for resource in \
  "$AUTOMATION_MANIFEST Deployment automation" \
  "$AUTOMATION_MANIFEST Deployment automation-worker" \
  "$GATEWAY_MANIFEST Deployment gateway"; do
  read -r file kind name <<<"$resource"
  require_resource_text "$file" "$kind" "$name" \
    '{ name: POLICY_FILE, value: "/config/policy-store.json" }'
  require_resource_text "$file" "$kind" "$name" \
    '{ name: POLICY_REFRESH_SECONDS, value: "5" }'
  require_resource_text "$file" "$kind" "$name" \
    "mountPath: /config"
  require_resource_text "$file" "$kind" "$name" \
    "readOnly: true"
  require_resource_text "$file" "$kind" "$name" \
    "name: polygate-routing-policy"
done

for resource in \
  "$AUTOMATION_MANIFEST Deployment automation-worker" \
  "$GATEWAY_MANIFEST Deployment gateway"; do
  read -r file kind name <<<"$resource"
  require_resource_text "$file" "$kind" "$name" \
    '{ name: POLICY_API_URL, value: "http://automation:8020" }'
done

require_resource_text \
  "$AUTOMATION_MANIFEST" Deployment automation \
  '{ name: POLICY_CONFIGMAP_NAME, value: "polygate-routing-policy" }'
require_resource_text \
  "$AUTOMATION_MANIFEST" Deployment automation \
  '{ name: POLICY_CONFIGMAP_KEY, value: "policy-store.json" }'
require_resource_text \
  "$AUTOMATION_MANIFEST" Deployment automation \
  "name: POD_NAMESPACE"
require_resource_text \
  "$AUTOMATION_MANIFEST" Deployment automation \
  "fieldPath: metadata.namespace"
require_resource_text \
  "$AUTOMATION_MANIFEST" Deployment automation \
  '{ name: POLICY_ADMIN_KEY_FILE, value: "/var/run/secrets/polygate-policy/admin-key" }'
require_resource_text \
  "$AUTOMATION_MANIFEST" Deployment automation \
  "mountPath: /var/run/secrets/polygate-policy"
require_resource_text \
  "$AUTOMATION_MANIFEST" Deployment automation \
  "secretName: polygate-policy-admin"

reject_text "$AUTOMATION_MANIFEST" "POLICY_ALLOW_ENV_ADMIN_KEY"
reject_text "$AUTOMATION_MANIFEST" "name: POLICY_ADMIN_KEY,"
reject_resource_text \
  "$AUTOMATION_MANIFEST" Deployment automation-worker \
  "POLICY_ADMIN_KEY"
reject_resource_text \
  "$GATEWAY_MANIFEST" Deployment gateway \
  "POLICY_ADMIN_KEY"

for file in "$RBAC_MANIFEST" "$AUTOMATION_MANIFEST" "$GATEWAY_MANIFEST"; do
  for service_name in \
    polygate-routing-policy \
    polygate-policy-admin \
    policy-admin; do
    reject_resource "$file" Service "$service_name"
  done
done

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

python3 - \
  "$RBAC_MANIFEST" \
  "$AUTOMATION_MANIFEST" \
  "$GATEWAY_MANIFEST" <<'PY'
import json
import subprocess
import sys


def load_documents(path):
    output = subprocess.check_output(
        [
            "kubectl",
            "create",
            "--dry-run=client",
            "--validate=false",
            f"--filename={path}",
            "--output=json",
        ],
        text=True,
    )
    decoder = json.JSONDecoder()
    documents = []
    cursor = 0
    while cursor < len(output):
        while cursor < len(output) and output[cursor].isspace():
            cursor += 1
        if cursor == len(output):
            break
        document, cursor = decoder.raw_decode(output, cursor)
        documents.append(document)
    return documents


def resource(documents, kind, name):
    matches = [
        document
        for document in documents
        if document.get("kind") == kind
        and document.get("metadata", {}).get("name") == name
    ]
    assert len(matches) == 1, (kind, name, len(matches))
    return matches[0]


rbac = load_documents(sys.argv[1])
automation_resources = load_documents(sys.argv[2])
gateway_resources = load_documents(sys.argv[3])

role = resource(rbac, "Role", "polygate-policy-controller")
assert role["rules"] == [
    {
        "apiGroups": [""],
        "resources": ["configmaps"],
        "resourceNames": ["polygate-routing-policy"],
        "verbs": ["get", "update"],
    }
]

binding = resource(rbac, "RoleBinding", "polygate-policy-controller")
assert binding["subjects"] == [
    {"kind": "ServiceAccount", "name": "polygate-policy-controller"}
]
assert binding["roleRef"] == {
    "apiGroup": "rbac.authorization.k8s.io",
    "kind": "Role",
    "name": "polygate-policy-controller",
}

deployments = {
    "automation": resource(automation_resources, "Deployment", "automation"),
    "automation-worker": resource(
        automation_resources, "Deployment", "automation-worker"
    ),
    "gateway": resource(gateway_resources, "Deployment", "gateway"),
}

for name, deployment in deployments.items():
    pod_spec = deployment["spec"]["template"]["spec"]
    container = pod_spec["containers"][0]
    policy_volumes = []
    for volume in pod_spec.get("volumes", []):
        sources = volume.get("projected", {}).get("sources", [])
        for source in sources:
            config_map = source.get("configMap", {})
            if config_map.get("name") == "polygate-routing-policy":
                policy_volumes.append(volume["name"])
    assert len(policy_volumes) == 1, (name, policy_volumes)

    mounts = [
        mount
        for mount in container.get("volumeMounts", [])
        if mount.get("name") == policy_volumes[0]
    ]
    assert len(mounts) == 1, (name, mounts)
    policy_mount = mounts[0]
    assert policy_mount["mountPath"] == "/config", (name, policy_mount)
    assert policy_mount.get("readOnly") is True, (name, policy_mount)
    assert "subPath" not in policy_mount, (name, policy_mount)

    env = {item["name"]: item.get("value") for item in container["env"]}
    assert env["POLICY_FILE"] == "/config/policy-store.json"
    assert env["POLICY_REFRESH_SECONDS"] == "5"

automation_spec = deployments["automation"]["spec"]["template"]["spec"]
assert automation_spec["serviceAccountName"] == "polygate-policy-controller"
assert automation_spec.get("automountServiceAccountToken", True) is True

worker_spec = deployments["automation-worker"]["spec"]["template"]["spec"]
assert worker_spec["automountServiceAccountToken"] is False
assert worker_spec.get("serviceAccountName") != "polygate-policy-controller"

for name in ("gateway", "automation-worker"):
    container = deployments[name]["spec"]["template"]["spec"]["containers"][0]
    env = {item["name"]: item.get("value") for item in container["env"]}
    assert env["POLICY_API_URL"] == "http://automation:8020"

automation_container = automation_spec["containers"][0]
admin_mounts = [
    mount
    for mount in automation_container["volumeMounts"]
    if mount["mountPath"] == "/var/run/secrets/polygate-policy"
]
assert admin_mounts == [
    {
        "name": "policy-admin",
        "mountPath": "/var/run/secrets/polygate-policy",
        "readOnly": True,
    }
]

for documents in (rbac, automation_resources, gateway_resources):
    for document in documents:
        if document.get("kind") == "Service":
            service_name = document.get("metadata", {}).get("name", "")
            assert "policy" not in service_name
            assert document.get("spec", {}).get("type", "ClusterIP") not in {
                "NodePort",
                "LoadBalancer",
            } or "policy" not in service_name
PY

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
