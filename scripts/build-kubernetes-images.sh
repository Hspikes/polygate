#!/usr/bin/env bash
# Build deployable Gateway and Mock Provider images from the current source.
# Set PUSH_IMAGES=1 only after authenticating Docker to the target registry.

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ECR_REGISTRY="${ECR_REGISTRY:-356029564744.dkr.ecr.us-east-1.amazonaws.com}"
TARGET_PLATFORM="${TARGET_PLATFORM:-linux/amd64}"
PUSH_IMAGES="${PUSH_IMAGES:-0}"

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

if [ "$PUSH_IMAGES" = "1" ]; then
  docker push "$GATEWAY_IMAGE"
  docker push "$MOCK_IMAGE"
else
  echo "Images were built locally only. Set PUSH_IMAGES=1 to push them."
fi

cat <<EOF

Prepared images:
  $GATEWAY_IMAGE
  $MOCK_IMAGE

Use the same values for deployment:
  ECR_REGISTRY=$ECR_REGISTRY
  IMAGE_TAG=$IMAGE_TAG
EOF
