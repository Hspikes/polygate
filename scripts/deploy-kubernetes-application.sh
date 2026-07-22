#!/usr/bin/env bash
# Deploy PolyGate application manifests with an explicit immutable image tag.

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ECR_REGISTRY="${ECR_REGISTRY:-356029564744.dkr.ecr.us-east-1.amazonaws.com}"
IMAGE_TAG="${IMAGE_TAG:?Set IMAGE_TAG to the tag pushed by build-kubernetes-images.sh}"
NAMESPACE="${NAMESPACE:-default}"

GATEWAY_IMAGE="$ECR_REGISTRY/polygate-gateway:$IMAGE_TAG"
MOCK_IMAGE="$ECR_REGISTRY/polygate-mock:$IMAGE_TAG"
WEB_IMAGE="$ECR_REGISTRY/polygate-web:$IMAGE_TAG"
PINNED_GATEWAY_IMAGE="356029564744.dkr.ecr.us-east-1.amazonaws.com/polygate-gateway:v2"
PINNED_MOCK_IMAGE="356029564744.dkr.ecr.us-east-1.amazonaws.com/polygate-mock:v1"
PINNED_WEB_IMAGE="356029564744.dkr.ecr.us-east-1.amazonaws.com/polygate-web:v1"

require_command() {
  if ! command -v "$1" >/dev/null 2>&1; then
    echo "Missing required command: $1" >&2
    exit 1
  fi
}

require_command kubectl
require_command sed

if [[ ! "$IMAGE_TAG" =~ ^[A-Za-z0-9_.-]+$ ]]; then
  echo "IMAGE_TAG contains unsupported characters: $IMAGE_TAG" >&2
  exit 1
fi
if ! grep -Fq "$PINNED_GATEWAY_IMAGE" "$ROOT_DIR/deploy/gateway.yaml"; then
  echo "Gateway manifest image anchor changed; update this deploy script." >&2
  exit 1
fi
if ! grep -Fq "$PINNED_MOCK_IMAGE" "$ROOT_DIR/deploy/mock-providers.yaml"; then
  echo "Mock manifest image anchor changed; update this deploy script." >&2
  exit 1
fi
if ! grep -Fq "$PINNED_WEB_IMAGE" "$ROOT_DIR/deploy/web.yaml"; then
  echo "Web manifest image anchor changed; update this deploy script." >&2
  exit 1
fi

echo "Using Kubernetes context: $(kubectl config current-context)"
echo "Deploying Gateway image: $GATEWAY_IMAGE"
echo "Deploying Mock image:    $MOCK_IMAGE"
echo "Deploying Web image:     $WEB_IMAGE"

if ! kubectl get --raw \
  "/apis/metrics.k8s.io/v1beta1/nodes" >/dev/null 2>&1; then
  echo "Kubernetes Metrics API is unavailable; HPA cannot operate." >&2
  echo "Install or repair metrics-server before deployment." >&2
  exit 1
fi

kubectl apply \
  --namespace "$NAMESPACE" \
  --filename "$ROOT_DIR/deploy/redis.yaml"

sed "s#$PINNED_MOCK_IMAGE#$MOCK_IMAGE#g" \
  "$ROOT_DIR/deploy/mock-providers.yaml" \
  | kubectl apply --namespace "$NAMESPACE" --filename=-

sed "s#$PINNED_GATEWAY_IMAGE#$GATEWAY_IMAGE#g" \
  "$ROOT_DIR/deploy/gateway.yaml" \
  | kubectl apply --namespace "$NAMESPACE" --filename=-

sed "s#$PINNED_WEB_IMAGE#$WEB_IMAGE#g" \
  "$ROOT_DIR/deploy/web.yaml" \
  | kubectl apply --namespace "$NAMESPACE" --filename=-

kubectl apply \
  --namespace "$NAMESPACE" \
  --filename "$ROOT_DIR/deploy/hpa.yaml"

kubectl rollout restart \
  deployment/gateway \
  --namespace "$NAMESPACE"

for deployment in redis mock-a mock-b gateway web; do
  kubectl rollout status \
    "deployment/$deployment" \
    --namespace "$NAMESPACE" \
    --timeout=180s
done

kubectl get deployments,services,hpa \
  --namespace "$NAMESPACE"

cat <<'EOF'

Application workloads are ready. The Web NodePort is the only public application entry point:

  kubectl get service web

Gateway and Mock Provider services remain internal. Deploy monitoring next:

  ./scripts/deploy-kubernetes-monitoring.sh
EOF
