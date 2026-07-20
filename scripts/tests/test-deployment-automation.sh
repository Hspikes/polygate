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
  "$ROOT_DIR/scripts/deploy-kubernetes-application.sh" \
  "PINNED_GATEWAY_IMAGE=\"$REGISTRY/polygate-gateway:v2\""
require_text \
  "$ROOT_DIR/scripts/deploy-kubernetes-application.sh" \
  "PINNED_MOCK_IMAGE=\"$REGISTRY/polygate-mock:v1\""

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
  "$ROOT_DIR/deploy/monitoring/README.md" \
  "ECR_REGISTRY=$REGISTRY"

echo "Deployment automation settings are aligned with the active EKS account."
