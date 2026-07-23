#!/usr/bin/env bash
# Regression checks for C-line deployment defaults. Run with:
#   bash scripts/tests/test-deployment-automation.sh

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
REGISTRY="356029564744.dkr.ecr.us-east-1.amazonaws.com"

require_text() {
  local file="$1"
  local expected="$2"

  if ! grep -Fq -- "$expected" "$file"; then
    echo "Missing expected deployment setting in $file: $expected" >&2
    exit 1
  fi
}

reject_text() {
  local file="$1"
  local rejected="$2"

  if grep -Fq -- "$rejected" "$file"; then
    echo "Found obsolete deployment setting in $file: $rejected" >&2
    exit 1
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
     $0 ~ "(^|\\n)metadata:\\n  name: " resource_name "(\\n|$)" {
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
    echo "Missing $resource_kind/$resource_name in $file" >&2
    exit 1
  fi
  if ! printf '%s\n' "$document" | grep -Fq -- "$expected"; then
    echo "Missing expected setting in $resource_kind/$resource_name: $expected" >&2
    exit 1
  fi
}

reject_resource() {
  local file="$1"
  local resource_kind="$2"
  local resource_name="$3"

  if resource_doc "$file" "$resource_kind" "$resource_name" >/dev/null 2>&1; then
    echo "Unexpected $resource_kind/$resource_name in $file" >&2
    exit 1
  fi
}

require_text \
  "$ROOT_DIR/scripts/build-kubernetes-images.sh" \
  "ECR_REGISTRY=\"\${ECR_REGISTRY:-$REGISTRY}\""
require_text \
  "$ROOT_DIR/scripts/build-kubernetes-images.sh" \
  "TARGET_PLATFORM=\"\${TARGET_PLATFORM:-linux/amd64}\""
require_text \
  "$ROOT_DIR/scripts/build-kubernetes-images.sh" \
  '--platform "$TARGET_PLATFORM"'
require_text \
  "$ROOT_DIR/scripts/build-kubernetes-images.sh" \
  'INCLUDE_AUTOMATION="${INCLUDE_AUTOMATION:-0}"'
require_text \
  "$ROOT_DIR/scripts/build-kubernetes-images.sh" \
  'AUTOMATION_IMAGE="$ECR_REGISTRY/polygate-automation:$IMAGE_TAG"'
require_text \
  "$ROOT_DIR/scripts/build-kubernetes-images.sh" \
  '--file "$ROOT_DIR/automation/Dockerfile"'
require_text \
  "$ROOT_DIR/scripts/build-kubernetes-images.sh" \
  'if [ "$INCLUDE_AUTOMATION" = "1" ]; then'

require_text \
  "$ROOT_DIR/scripts/deploy-kubernetes-application.sh" \
  "PINNED_GATEWAY_IMAGE=\"$REGISTRY/polygate-gateway:v2\""
require_text \
  "$ROOT_DIR/scripts/deploy-kubernetes-application.sh" \
  "PINNED_MOCK_IMAGE=\"$REGISTRY/polygate-mock:v1\""
require_text \
  "$ROOT_DIR/scripts/deploy-kubernetes-application.sh" \
  "PINNED_WEB_IMAGE=\"$REGISTRY/polygate-web:v1\""
require_text \
  "$ROOT_DIR/scripts/deploy-kubernetes-application.sh" \
  'INCLUDE_AUTOMATION="${INCLUDE_AUTOMATION:-0}"'
require_text \
  "$ROOT_DIR/scripts/deploy-kubernetes-application.sh" \
  "PINNED_AUTOMATION_IMAGE=\"$REGISTRY/polygate-automation:v1\""
require_text \
  "$ROOT_DIR/scripts/deploy-kubernetes-application.sh" \
  'AUTOMATION_IMAGE="$ECR_REGISTRY/polygate-automation:$IMAGE_TAG"'
require_text \
  "$ROOT_DIR/scripts/deploy-kubernetes-application.sh" \
  '"$ROOT_DIR/deploy/automation.yaml"'
require_text \
  "$ROOT_DIR/scripts/build-kubernetes-images.sh" \
  '"$ROOT_DIR/web"'
require_text \
  "$ROOT_DIR/deploy/gateway.yaml" \
  "type: ClusterIP"
require_text \
  "$ROOT_DIR/deploy/web.yaml" \
  "type: NodePort"

require_text \
  "$ROOT_DIR/scripts/kubernetes-monitoring-preflight.sh" \
  '-kubernetes-version 1.34.0'
require_text \
  "$ROOT_DIR/scripts/kubernetes-monitoring-preflight.sh" \
  "$REGISTRY/polygate-gateway:v2"
require_text \
  "$ROOT_DIR/scripts/kubernetes-monitoring-preflight.sh" \
  "$REGISTRY/polygate-mock:v1"
require_text \
  "$ROOT_DIR/scripts/kubernetes-monitoring-preflight.sh" \
  "$REGISTRY/polygate-web:v1"

require_text \
  "$ROOT_DIR/deploy/monitoring/README.md" \
  "ECR_REGISTRY=$REGISTRY"
require_text \
  "$ROOT_DIR/deploy/RUNBOOK.md" \
  'update-kubeconfig --name G3EKS'
require_text \
  "$ROOT_DIR/deploy/README.md" \
  'INCLUDE_AUTOMATION=1'
require_text \
  "$ROOT_DIR/deploy/README.md" \
  'C1 不立即部署到 EKS'

require_text \
  "$ROOT_DIR/deploy/automation.yaml" \
  "$REGISTRY/polygate-automation:v1"
require_text \
  "$ROOT_DIR/deploy/automation.yaml" \
  "replicas: 1"
require_text \
  "$ROOT_DIR/deploy/automation.yaml" \
  "type: ClusterIP"
require_text \
  "$ROOT_DIR/deploy/automation.yaml" \
  "automountServiceAccountToken: false"
require_text \
  "$ROOT_DIR/deploy/automation.yaml" \
  "runAsUser: 10001"
require_text \
  "$ROOT_DIR/deploy/automation.yaml" \
  'value: "http://gateway:8000"'
require_text \
  "$ROOT_DIR/deploy/automation.yaml" \
  'value: "redis://redis:6379/0"'
require_text \
  "$ROOT_DIR/deploy/automation.yaml" \
  "httpGet: { path: /ready, port: http }"
require_text \
  "$ROOT_DIR/deploy/automation.yaml" \
  "name: automation-worker"
require_text \
  "$ROOT_DIR/deploy/automation.yaml" \
  "type: Recreate"
require_text \
  "$ROOT_DIR/deploy/automation.yaml" \
  'command: ["python", "-m", "automation.app.worker"]'
require_text \
  "$ROOT_DIR/deploy/automation.yaml" \
  "ports: [{ name: metrics, containerPort: 9000 }]"
require_text \
  "$ROOT_DIR/deploy/automation.yaml" \
  '{ name: WORKER_CONCURRENCY, value: "1" }'
require_text \
  "$ROOT_DIR/deploy/automation.yaml" \
  "name: POLYGATE_API_KEY"
require_text \
  "$ROOT_DIR/deploy/automation.yaml" \
  "secretKeyRef: { name: gateway-client-secrets, key: worker-api-key, optional: true }"
require_text \
  "$ROOT_DIR/deploy/automation.yaml" \
  "terminationGracePeriodSeconds: 60"
require_text \
  "$ROOT_DIR/scripts/deploy-kubernetes-application.sh" \
  'deployments+=(automation automation-worker)'
require_text \
  "$ROOT_DIR/monitoring/prometheus/prometheus-kubernetes.yml" \
  "job_name: polygate-automation-worker"
require_text \
  "$ROOT_DIR/monitoring/prometheus/prometheus-kubernetes.yml" \
  "regex: automation-worker"
require_text \
  "$ROOT_DIR/monitoring/prometheus/prometheus-kubernetes.yml" \
  'regex: "9000"'
require_text \
  "$ROOT_DIR/scripts/kubernetes-automation-smoke-test.sh" \
  'AUTOMATION_URL="${AUTOMATION_URL:-http://localhost:8020}"'
require_text \
  "$ROOT_DIR/scripts/kubernetes-automation-smoke-test.sh" \
  'WORKER_METRICS_URL="${WORKER_METRICS_URL:-http://localhost:9000}"'
require_text \
  "$ROOT_DIR/scripts/kubernetes-automation-smoke-test.sh" \
  'automation_worker_queue_depth'
require_text \
  "$ROOT_DIR/scripts/kubernetes-automation-smoke-test.sh" \
  'status" = "completed"'
require_text \
  "$ROOT_DIR/scripts/kubernetes-monitoring-smoke-test.sh" \
  'INCLUDE_AUTOMATION="${INCLUDE_AUTOMATION:-0}"'
require_text \
  "$ROOT_DIR/scripts/kubernetes-monitoring-smoke-test.sh" \
  'up{job="polygate-automation-worker"}'
require_text \
  "$ROOT_DIR/scripts/kubernetes-monitoring-smoke-test.sh" \
  'automation_worker_queue_depth'
require_text \
  "$ROOT_DIR/deploy/README.md" \
  'kubectl port-forward deployment/automation-worker 9000:9000'
require_text \
  "$ROOT_DIR/deploy/README.md" \
  'uvicorn automation.app.main:get_app --factory --host 0.0.0.0 --port 8020'
reject_text \
  "$ROOT_DIR/deploy/README.md" \
  'uvicorn automation.app.main:app'

require_resource_text \
  "$ROOT_DIR/deploy/automation.yaml" \
  Deployment automation \
  "replicas: 1"
require_resource_text \
  "$ROOT_DIR/deploy/automation.yaml" \
  Deployment automation \
  "httpGet: { path: /ready, port: http }"
require_resource_text \
  "$ROOT_DIR/deploy/automation.yaml" \
  Deployment automation-worker \
  "replicas: 1"
require_resource_text \
  "$ROOT_DIR/deploy/automation.yaml" \
  Deployment automation-worker \
  "type: Recreate"
require_resource_text \
  "$ROOT_DIR/deploy/automation.yaml" \
  Deployment automation-worker \
  'command: ["python", "-m", "automation.app.worker"]'
require_resource_text \
  "$ROOT_DIR/deploy/automation.yaml" \
  Deployment automation-worker \
  "ports: [{ name: metrics, containerPort: 9000 }]"
require_resource_text \
  "$ROOT_DIR/deploy/automation.yaml" \
  Deployment automation-worker \
  "secretKeyRef: { name: gateway-client-secrets, key: worker-api-key, optional: true }"
require_resource_text \
  "$ROOT_DIR/deploy/automation.yaml" \
  Service automation \
  "type: ClusterIP"
reject_resource \
  "$ROOT_DIR/deploy/automation.yaml" \
  Service automation-worker

require_text \
  "$ROOT_DIR/docker-compose.yml" \
  'AUTOMATION_REDIS_URL: redis://redis:6379/0'
require_text \
  "$ROOT_DIR/docs/superpowers/specs/2026-07-23-c1-automation-kubernetes-design.md" \
  'AUTOMATION_REDIS_URL=redis://redis:6379/0'
require_text \
  "$ROOT_DIR/docs/superpowers/plans/2026-07-23-c1-automation-kubernetes.md" \
  'AUTOMATION_REDIS_URL=redis://redis:6379/0'
require_text \
  "$ROOT_DIR/scripts/kubernetes-monitoring-preflight.sh" \
  '"$ROOT_DIR/deploy/automation.yaml"'

for file in \
  "$ROOT_DIR/deploy/automation.yaml" \
  "$ROOT_DIR/docker-compose.yml" \
  "$ROOT_DIR/docs/superpowers/specs/2026-07-23-c1-automation-kubernetes-design.md" \
  "$ROOT_DIR/docs/superpowers/plans/2026-07-23-c1-automation-kubernetes.md"; do
  reject_text "$file" 'redis://redis:6379/1'
done

echo "Deployment automation settings are aligned with the active EKS account."
