#!/usr/bin/env bash
# Deploy PolyGate application manifests with an explicit immutable image tag.

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ECR_REGISTRY="${ECR_REGISTRY:-356029564744.dkr.ecr.us-east-1.amazonaws.com}"
INCLUDE_AUTOMATION="${INCLUDE_AUTOMATION:-0}"
POLICY_CONFIGMAP="polygate-routing-policy"
POLICY_ADMIN_SECRET="polygate-policy-admin"

if [ "$INCLUDE_AUTOMATION" != "0" ] && [ "$INCLUDE_AUTOMATION" != "1" ]; then
  echo "INCLUDE_AUTOMATION must be 0 or 1." >&2
  exit 1
fi

IMAGE_TAG="${IMAGE_TAG:?Set IMAGE_TAG to the tag pushed by build-kubernetes-images.sh}"
NAMESPACE="${NAMESPACE:-default}"

GATEWAY_IMAGE="$ECR_REGISTRY/polygate-gateway:$IMAGE_TAG"
MOCK_IMAGE="$ECR_REGISTRY/polygate-mock:$IMAGE_TAG"
WEB_IMAGE="$ECR_REGISTRY/polygate-web:$IMAGE_TAG"
AUTOMATION_IMAGE="$ECR_REGISTRY/polygate-automation:$IMAGE_TAG"
PINNED_GATEWAY_IMAGE="356029564744.dkr.ecr.us-east-1.amazonaws.com/polygate-gateway:v2"
PINNED_MOCK_IMAGE="356029564744.dkr.ecr.us-east-1.amazonaws.com/polygate-mock:v1"
PINNED_WEB_IMAGE="356029564744.dkr.ecr.us-east-1.amazonaws.com/polygate-web:v1"
PINNED_AUTOMATION_IMAGE="356029564744.dkr.ecr.us-east-1.amazonaws.com/polygate-automation:v1"

require_command() {
  if ! command -v "$1" >/dev/null 2>&1; then
    echo "Missing required command: $1" >&2
    exit 1
  fi
}

require_command kubectl
require_command python3
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
if [ "$INCLUDE_AUTOMATION" = "1" ] \
  && ! grep -Fq "$PINNED_AUTOMATION_IMAGE" "$ROOT_DIR/deploy/automation.yaml"; then
  echo "Automation manifest image anchor changed; update this deploy script." >&2
  exit 1
fi

GATEWAY_CLIENT_SECRET="gateway-client-secrets"
if ! GATEWAY_CLIENT_SECRET_KEYS="$(
  kubectl get secret "$GATEWAY_CLIENT_SECRET" \
    --namespace "$NAMESPACE" \
    --output=go-template='{{range $key, $value := .data}}{{printf "%s\n" $key}}{{end}}'
)"; then
  echo "Missing Secret $NAMESPACE/$GATEWAY_CLIENT_SECRET." >&2
  echo "Create it with api-keys and web-api-key before deployment." >&2
  if [ "$INCLUDE_AUTOMATION" = "1" ]; then
    echo "INCLUDE_AUTOMATION=1 also requires worker-api-key." >&2
  fi
  exit 1
fi

required_gateway_secret_keys=(api-keys web-api-key)
if [ "$INCLUDE_AUTOMATION" = "1" ]; then
  required_gateway_secret_keys+=(worker-api-key)
fi

for secret_key in "${required_gateway_secret_keys[@]}"; do
  if ! grep -Fxq -- "$secret_key" <<<"$GATEWAY_CLIENT_SECRET_KEYS"; then
    echo \
      "Secret $NAMESPACE/$GATEWAY_CLIENT_SECRET is missing required key: $secret_key" \
      >&2
    exit 1
  fi
done

if [ "$INCLUDE_AUTOMATION" = "1" ]; then
  if ! POLICY_ADMIN_SECRET_KEYS="$(
    kubectl get secret "$POLICY_ADMIN_SECRET" \
      --namespace "$NAMESPACE" \
      --output=go-template='{{range $key, $value := .data}}{{printf "%s\n" $key}}{{end}}'
  )"; then
    echo "Missing Secret $NAMESPACE/$POLICY_ADMIN_SECRET." >&2
    echo "Create it with admin-key before deploying Automation." >&2
    exit 1
  fi
  if ! grep -Fxq -- "admin-key" <<<"$POLICY_ADMIN_SECRET_KEYS"; then
    echo \
      "Secret $NAMESPACE/$POLICY_ADMIN_SECRET is missing required key: admin-key" \
      >&2
    exit 1
  fi
  if ! POLICY_ADMIN_KEY_LENGTH="$(
    kubectl get secret "$POLICY_ADMIN_SECRET" \
      --namespace "$NAMESPACE" \
      --output=go-template='{{len (index .data "admin-key")}}'
  )"; then
    echo \
      "Unable to verify Secret $NAMESPACE/$POLICY_ADMIN_SECRET admin-key." \
      >&2
    exit 1
  fi
  if ! [[ "$POLICY_ADMIN_KEY_LENGTH" =~ ^[1-9][0-9]*$ ]]; then
    echo \
      "Secret $NAMESPACE/$POLICY_ADMIN_SECRET has an empty required key: admin-key" \
      >&2
    exit 1
  fi
  unset POLICY_ADMIN_KEY_LENGTH
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

POLICY_STORE_TMP="$(mktemp "${TMPDIR:-/tmp}/polygate-policy-store.XXXXXX")"
trap 'rm -f "$POLICY_STORE_TMP"' EXIT

if ! kubectl get configmap "$POLICY_CONFIGMAP" \
  --namespace "$NAMESPACE" >/dev/null 2>&1; then
  python3 "$ROOT_DIR/scripts/render-default-policy-store.py" \
    "$ROOT_DIR/contracts/policy-examples.json" \
    >"$POLICY_STORE_TMP"
  kubectl create configmap "$POLICY_CONFIGMAP" \
    --namespace "$NAMESPACE" \
    --from-file="policy-store.json=$POLICY_STORE_TMP"
  echo "Created initial polygate-routing-policy ConfigMap"
else
  echo "Preserving existing polygate-routing-policy ConfigMap and version history"
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

if [ "$INCLUDE_AUTOMATION" = "1" ]; then
  echo "Deploying Automation image: $AUTOMATION_IMAGE"
  kubectl apply \
    --namespace "$NAMESPACE" \
    --filename "$ROOT_DIR/deploy/policy-rbac.yaml"
  sed "s#$PINNED_AUTOMATION_IMAGE#$AUTOMATION_IMAGE#g" \
    "$ROOT_DIR/deploy/automation.yaml" \
    | kubectl apply --namespace "$NAMESPACE" --filename=-
fi

kubectl apply \
  --namespace "$NAMESPACE" \
  --filename "$ROOT_DIR/deploy/hpa.yaml"

kubectl rollout restart \
  deployment/gateway \
  --namespace "$NAMESPACE"

deployments=(redis mock-a mock-b gateway web)
if [ "$INCLUDE_AUTOMATION" = "1" ]; then
  deployments+=(automation automation-worker)
fi

for deployment in "${deployments[@]}"; do
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

Gateway, Automation, and Mock Provider services remain internal. Deploy monitoring next:

  ./scripts/deploy-kubernetes-monitoring.sh
EOF
