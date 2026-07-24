#!/usr/bin/env bash
# Deploy the prepared monitoring stack to the current Kubernetes context.
# The application workloads are expected in the default namespace.

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
NAMESPACE="default"
MANIFEST_DIR="$ROOT_DIR/deploy/monitoring"

require_command() {
  if ! command -v "$1" >/dev/null 2>&1; then
    echo "Missing required command: $1" >&2
    exit 1
  fi
}

apply_file_configmap() {
  local name="$1"
  local key="$2"
  local source="$3"

  kubectl create configmap "$name" \
    --namespace "$NAMESPACE" \
    --from-file="$key=$source" \
    --dry-run=client \
    --output=yaml \
    | kubectl apply --filename=-
}

require_command kubectl

if ! kubectl get secret polygate-grafana-admin \
  --namespace "$NAMESPACE" >/dev/null 2>&1; then
  cat >&2 <<'EOF'
Missing Secret default/polygate-grafana-admin.
Create it without committing the password:

  kubectl create secret generic polygate-grafana-admin \
    --namespace default \
    --from-literal=admin-user=admin \
    --from-literal=admin-password='<choose-a-strong-password>'
EOF
  exit 1
fi

echo "Using Kubernetes context: $(kubectl config current-context)"
echo "Updating version-controlled monitoring ConfigMaps"

kubectl create configmap polygate-prometheus-config \
  --namespace "$NAMESPACE" \
  --from-file="prometheus.yml=$ROOT_DIR/monitoring/prometheus/prometheus-kubernetes.yml" \
  --from-file="polygate-rules.yml=$ROOT_DIR/monitoring/prometheus/polygate-rules.yml" \
  --dry-run=client \
  --output=yaml \
  | kubectl apply --filename=-
apply_file_configmap \
  polygate-grafana-datasource \
  prometheus.yml \
  "$ROOT_DIR/monitoring/grafana/provisioning/datasources/prometheus.yml"
apply_file_configmap \
  polygate-grafana-dashboard-provider \
  polygate.yml \
  "$ROOT_DIR/monitoring/grafana/provisioning/dashboards/polygate.yml"
apply_file_configmap \
  polygate-grafana-dashboard \
  polygate-overview.json \
  "$ROOT_DIR/monitoring/grafana/dashboards/polygate-overview.json"

echo "Applying monitoring workloads"
kubectl apply \
  --namespace "$NAMESPACE" \
  --kustomize "$MANIFEST_DIR"

kubectl rollout restart \
  deployment/prometheus deployment/grafana \
  --namespace "$NAMESPACE"

for deployment in kube-state-metrics prometheus grafana; do
  kubectl rollout status \
    "deployment/$deployment" \
    --namespace "$NAMESPACE" \
    --timeout=180s
done

kubectl get pods,services \
  --namespace "$NAMESPACE" \
  --selector='app in (kube-state-metrics,prometheus,grafana)'

cat <<'EOF'

Monitoring workloads are ready. Keep them private and access them locally:

  kubectl port-forward service/prometheus 9090:9090
  kubectl port-forward service/grafana 3000:3000

Then open:
  Prometheus targets: http://localhost:9090/targets
  Grafana:            http://localhost:3000

Use scripts/kubernetes-monitoring-smoke-test.sh after starting both
port-forwards to verify the deployed data path.
EOF
