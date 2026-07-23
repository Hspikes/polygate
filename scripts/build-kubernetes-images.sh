#!/usr/bin/env bash
# Build deployable Web, Gateway and Mock Provider images from the current source.
# Automation is opt-in. Set PUSH_IMAGES=1 only after registry authentication.

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ECR_REGISTRY="${ECR_REGISTRY:-356029564744.dkr.ecr.us-east-1.amazonaws.com}"
TARGET_PLATFORM="${TARGET_PLATFORM:-linux/amd64}"
PUSH_IMAGES="${PUSH_IMAGES:-0}"
INCLUDE_AUTOMATION="${INCLUDE_AUTOMATION:-0}"

if [ "$INCLUDE_AUTOMATION" != "0" ] && [ "$INCLUDE_AUTOMATION" != "1" ]; then
  echo "INCLUDE_AUTOMATION must be 0 or 1." >&2
  exit 1
fi

if [ -z "${IMAGE_TAG:-}" ]; then
  if [ -n "$(git -C "$ROOT_DIR" status --porcelain)" ]; then
    echo "Commit or clean the worktree before deriving an image tag." >&2
    echo "For a disposable local build, set IMAGE_TAG explicitly." >&2
    exit 1
  fi
  IMAGE_TAG="$(git -C "$ROOT_DIR" rev-parse --short=12 HEAD)"
fi

GATEWAY_IMAGE="$ECR_REGISTRY/polygate-gateway:$IMAGE_TAG"
MOCK_IMAGE="$ECR_REGISTRY/polygate-mock:$IMAGE_TAG"
WEB_IMAGE="$ECR_REGISTRY/polygate-web:$IMAGE_TAG"
AUTOMATION_IMAGE="$ECR_REGISTRY/polygate-automation:$IMAGE_TAG"

docker build \
  --platform "$TARGET_PLATFORM" \
  --pull \
  --tag "$GATEWAY_IMAGE" \
  "$ROOT_DIR/gateway"
docker build \
  --platform "$TARGET_PLATFORM" \
  --pull \
  --tag "$MOCK_IMAGE" \
  "$ROOT_DIR/providers/mock"
docker build \
  --platform "$TARGET_PLATFORM" \
  --pull \
  --tag "$WEB_IMAGE" \
  "$ROOT_DIR/web"
if [ "$INCLUDE_AUTOMATION" = "1" ]; then
  docker build \
    --platform "$TARGET_PLATFORM" \
    --pull \
    --file "$ROOT_DIR/automation/Dockerfile" \
    --tag "$AUTOMATION_IMAGE" \
    "$ROOT_DIR"
fi

if [ "$PUSH_IMAGES" = "1" ]; then
  docker push "$GATEWAY_IMAGE"
  docker push "$MOCK_IMAGE"
  docker push "$WEB_IMAGE"
  if [ "$INCLUDE_AUTOMATION" = "1" ]; then
    docker push "$AUTOMATION_IMAGE"
  fi
else
  echo "Images were built locally only. Set PUSH_IMAGES=1 to push them."
fi

cat <<EOF

Prepared images:
  $GATEWAY_IMAGE
  $MOCK_IMAGE
  $WEB_IMAGE
EOF

if [ "$INCLUDE_AUTOMATION" = "1" ]; then
  echo "  $AUTOMATION_IMAGE"
fi

cat <<EOF

Use the same values for deployment:
  ECR_REGISTRY=$ECR_REGISTRY
  IMAGE_TAG=$IMAGE_TAG
EOF
