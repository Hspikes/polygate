#!/usr/bin/env bash
# Verify the deployed Kubernetes monitoring path through local port-forwards.

set -uo pipefail

PROMETHEUS="${PROMETHEUS:-http://localhost:9090}"
GRAFANA="${GRAFANA:-http://localhost:3000}"
GRAFANA_USER="${GRAFANA_USER:-admin}"
GRAFANA_PASSWORD="${GRAFANA_PASSWORD:-}"

PASS=0
FAIL=0

ok() {
  PASS=$((PASS + 1))
  echo "  OK  $1"
}

bad() {
  FAIL=$((FAIL + 1))
  echo "  FAIL  $1"
}

query_value() {
  local expression="$1"
  curl -fsS --get \
    --data-urlencode "query=$expression" \
    "$PROMETHEUS/api/v1/query" \
    | python3 -c '
import json
import sys

payload = json.load(sys.stdin)
results = payload.get("data", {}).get("result", [])
if payload.get("status") != "success" or not results:
    raise SystemExit(1)
print(results[0]["value"][1])
'
}

positive_number() {
  python3 -c "
import math
value = float('$1')
raise SystemExit(0 if math.isfinite(value) and value > 0 else 1)
"
}

echo "PolyGate Kubernetes monitoring smoke test"
echo "Prometheus: $PROMETHEUS"
echo "Grafana:    $GRAFANA"

if curl -fsS "$PROMETHEUS/-/ready" >/dev/null 2>&1; then
  ok "Prometheus is ready"
else
  bad "Prometheus is not ready"
fi

GATEWAY_TARGETS="$(
  query_value 'count(up{job="polygate-gateway"})' 2>/dev/null || echo 0
)"
GATEWAY_UP="$(
  query_value 'sum(up{job="polygate-gateway"})' 2>/dev/null || echo 0
)"
if positive_number "$GATEWAY_TARGETS" \
  && python3 -c "raise SystemExit(0 if float('$GATEWAY_UP') == float('$GATEWAY_TARGETS') else 1)"; then
  ok "Every discovered Gateway Pod is UP ($GATEWAY_UP/$GATEWAY_TARGETS)"
else
  bad "Gateway scrape targets are incomplete ($GATEWAY_UP/$GATEWAY_TARGETS)"
fi

KSM_UP="$(
  query_value 'max(up{job="kube-state-metrics"})' 2>/dev/null || echo 0
)"
if python3 -c "raise SystemExit(0 if float('$KSM_UP') == 1 else 1)"; then
  ok "kube-state-metrics is UP"
else
  bad "kube-state-metrics is not UP"
fi

CADVISOR_MIN="$(
  query_value 'min(up{job="kubernetes-cadvisor"})' 2>/dev/null || echo 0
)"
CADVISOR_COUNT="$(
  query_value 'count(up{job="kubernetes-cadvisor"})' 2>/dev/null || echo 0
)"
if python3 -c "raise SystemExit(0 if float('$CADVISOR_MIN') == 1 else 1)" \
  && positive_number "$CADVISOR_COUNT"; then
  ok "Every discovered node cAdvisor target is UP ($CADVISOR_COUNT nodes)"
else
  bad "One or more node cAdvisor targets are unavailable"
fi

AVAILABLE_REPLICAS="$(
  query_value \
    'max(kube_deployment_status_replicas_available{namespace="default",deployment="gateway"})' \
    2>/dev/null || echo 0
)"
DESIRED_REPLICAS="$(
  query_value \
    'max(kube_horizontalpodautoscaler_status_desired_replicas{namespace="default",horizontalpodautoscaler="gateway-hpa"})' \
    2>/dev/null || echo 0
)"
if positive_number "$AVAILABLE_REPLICAS" \
  && positive_number "$DESIRED_REPLICAS"; then
  ok "Deployment and HPA replica metrics are present ($AVAILABLE_REPLICAS available, $DESIRED_REPLICAS desired)"
else
  bad "Deployment or HPA replica metrics are missing"
fi

CPU_SERIES="$(
  query_value \
    'count(container_cpu_usage_seconds_total{namespace="default",pod=~"gateway-.*",container="gateway"})' \
    2>/dev/null || echo 0
)"
MEMORY_SERIES="$(
  query_value \
    'count(container_memory_working_set_bytes{namespace="default",pod=~"gateway-.*",container="gateway"})' \
    2>/dev/null || echo 0
)"
if positive_number "$CPU_SERIES" && positive_number "$MEMORY_SERIES"; then
  ok "Gateway CPU and memory series are present"
else
  bad "Gateway CPU or memory series are missing"
fi

if curl -fsS "$GRAFANA/api/health" >/dev/null 2>&1; then
  ok "Grafana is ready"
else
  bad "Grafana is not ready"
fi

if [ -z "$GRAFANA_PASSWORD" ]; then
  bad "Set GRAFANA_PASSWORD to verify the protected dashboard"
else
  DASHBOARD="$(
    curl -fsS \
      --user "$GRAFANA_USER:$GRAFANA_PASSWORD" \
      "$GRAFANA/api/dashboards/uid/polygate-overview" \
      2>/dev/null || echo '{}'
  )"
  if echo "$DASHBOARD" | python3 -c '
import json
import sys

dashboard = json.load(sys.stdin).get("dashboard", {})
panels = dashboard.get("panels", [])
expressions = [
    target.get("expr", "")
    for panel in panels
    for target in panel.get("targets", [])
]
required = {
    "container_cpu_usage_seconds_total",
    "container_memory_working_set_bytes",
    "kube_deployment_status_replicas_available",
    "kube_horizontalpodautoscaler_status_desired_replicas",
}
valid = (
    len(panels) >= 20
    and all(
        any(metric in expression for expression in expressions)
        for metric in required
    )
)
raise SystemExit(0 if valid else 1)
'; then
    ok "The protected Grafana dashboard contains Kubernetes resource panels"
  else
    bad "The Grafana dashboard is missing or authentication failed"
  fi
fi

echo
echo "$PASS passed, $FAIL failed"

if [ "$FAIL" -gt 0 ]; then
  exit 1
fi
