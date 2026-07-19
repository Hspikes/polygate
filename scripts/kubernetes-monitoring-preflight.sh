#!/usr/bin/env bash
# Offline/local validation for the Kubernetes monitoring deployment assets.

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
MANIFEST_DIR="$ROOT_DIR/deploy/monitoring"
PROMETHEUS_CONFIG="$ROOT_DIR/monitoring/prometheus/prometheus-kubernetes.yml"
DASHBOARD="$ROOT_DIR/monitoring/grafana/dashboards/polygate-overview.json"
GRAFANA_DATASOURCE="$ROOT_DIR/monitoring/grafana/provisioning/datasources/prometheus.yml"
GRAFANA_DASHBOARD_PROVIDER="$ROOT_DIR/monitoring/grafana/provisioning/dashboards/polygate.yml"
KUBECONFORM_IMAGE="ghcr.io/yannh/kubeconform:v0.7.0"
PROMETHEUS_IMAGE="prom/prometheus:v3.13.1"

PASS=0

ok() {
  PASS=$((PASS + 1))
  echo "  OK  $1"
}

require_command() {
  if ! command -v "$1" >/dev/null 2>&1; then
    echo "Missing required command: $1" >&2
    exit 1
  fi
}

require_command docker
require_command kubectl
require_command python3

echo "PolyGate Kubernetes monitoring preflight"

kubectl kustomize "$MANIFEST_DIR" >/dev/null
ok "Kustomize renders the monitoring resources without a cluster"

for manifest in \
  "$ROOT_DIR/deploy/redis.yaml" \
  "$ROOT_DIR/deploy/mock-providers.yaml" \
  "$ROOT_DIR/deploy/gateway.yaml" \
  "$ROOT_DIR/deploy/hpa.yaml"; do
  docker run --rm --interactive "$KUBECONFORM_IMAGE" \
    -strict \
    -summary \
    -kubernetes-version 1.35.0 < "$manifest"
done
ok "All application Kubernetes manifests pass schema validation"

kubectl kustomize "$MANIFEST_DIR" \
  | docker run --rm --interactive "$KUBECONFORM_IMAGE" \
      -strict \
      -summary \
      -kubernetes-version 1.35.0
ok "All rendered Kubernetes resources pass schema validation"

docker run --rm \
  --entrypoint promtool \
  --volume "$PROMETHEUS_CONFIG:/etc/prometheus/prometheus.yml:ro" \
  --volume "/dev/null:/var/run/secrets/kubernetes.io/serviceaccount/token:ro" \
  --volume "/etc/ssl/certs/ca-certificates.crt:/var/run/secrets/kubernetes.io/serviceaccount/ca.crt:ro" \
  "$PROMETHEUS_IMAGE" \
  check config /etc/prometheus/prometheus.yml
ok "The in-cluster Prometheus configuration passes promtool"

kubectl create configmap polygate-prometheus-config \
  --from-file="prometheus.yml=$PROMETHEUS_CONFIG" \
  --dry-run=client \
  --output=yaml >/dev/null
kubectl create configmap polygate-grafana-dashboard \
  --from-file="polygate-overview.json=$DASHBOARD" \
  --dry-run=client \
  --output=yaml >/dev/null
kubectl create configmap polygate-grafana-datasource \
  --from-file="prometheus.yml=$GRAFANA_DATASOURCE" \
  --dry-run=client \
  --output=yaml >/dev/null
kubectl create configmap polygate-grafana-dashboard-provider \
  --from-file="polygate.yml=$GRAFANA_DASHBOARD_PROVIDER" \
  --dry-run=client \
  --output=yaml >/dev/null
ok "Prometheus and Grafana source files render as ConfigMaps"

python3 - "$DASHBOARD" <<'PY'
import json
import sys

with open(sys.argv[1], encoding="utf-8") as source:
    dashboard = json.load(source)

panels = dashboard.get("panels", [])
panel_ids = [panel.get("id") for panel in panels]
if len(panel_ids) != len(set(panel_ids)):
    raise SystemExit("dashboard contains duplicate panel IDs")

expressions = [
    target["expr"]
    for panel in panels
    for target in panel.get("targets", [])
    if target.get("expr")
]
required_metrics = {
    "container_cpu_usage_seconds_total",
    "container_memory_working_set_bytes",
    "kube_deployment_status_replicas_available",
    "kube_horizontalpodautoscaler_status_desired_replicas",
}
missing = {
    metric
    for metric in required_metrics
    if not any(metric in expression for expression in expressions)
}
if missing:
    raise SystemExit(f"dashboard is missing Kubernetes metrics: {sorted(missing)}")
PY
ok "The dashboard contains unique panels and required resource queries"

python3 - "$DASHBOARD" <<'PY' \
  | docker run --rm --interactive --entrypoint promtool \
      "$PROMETHEUS_IMAGE" check rules /dev/stdin
import json
import sys

with open(sys.argv[1], encoding="utf-8") as source:
    dashboard = json.load(source)

rules = []
for panel_index, panel in enumerate(dashboard.get("panels", [])):
    for target_index, target in enumerate(panel.get("targets", [])):
        expression = target.get("expr")
        if expression:
            rules.append(
                {
                    "record": (
                        f"polygate_dashboard_query_"
                        f"{panel_index}_{target_index}"
                    ),
                    "expr": expression.replace("$__rate_interval", "5m"),
                }
            )

print(
    json.dumps(
        {
            "groups": [
                {
                    "name": "dashboard-query-validation",
                    "rules": rules,
                }
            ]
        }
    )
)
PY
ok "All Grafana PromQL expressions pass promtool syntax validation"

if grep -R --include="*.yaml" "monitoring-api" \
  "$MANIFEST_DIR" >/dev/null 2>&1; then
  echo "Monitoring API must remain out of the Kubernetes deployment" >&2
  exit 1
fi
ok "Monitoring API is intentionally absent from Kubernetes manifests"

if grep -Fq \
  "896133844534.dkr.ecr.us-east-1.amazonaws.com/polygate-gateway:v1" \
  "$ROOT_DIR/deploy/gateway.yaml" \
  && grep -Fq \
    "896133844534.dkr.ecr.us-east-1.amazonaws.com/polygate-mock:v1" \
    "$ROOT_DIR/deploy/mock-providers.yaml"; then
  ok "Application deployment script can replace both pinned image anchors"
else
  echo "Pinned application image anchors changed unexpectedly" >&2
  exit 1
fi

echo
echo "$PASS local pre-deployment checks passed."
