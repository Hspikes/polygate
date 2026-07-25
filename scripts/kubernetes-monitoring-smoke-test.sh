#!/usr/bin/env bash
# Verify the deployed Kubernetes monitoring path through local port-forwards.

set -uo pipefail

PROMETHEUS="${PROMETHEUS:-http://localhost:9090}"
GRAFANA="${GRAFANA:-http://localhost:3000}"
GRAFANA_USER="${GRAFANA_USER:-admin}"
GRAFANA_PASSWORD="${GRAFANA_PASSWORD:-}"
INCLUDE_AUTOMATION="${INCLUDE_AUTOMATION:-0}"
INCLUDE_POLICY="${INCLUDE_POLICY:-0}"

if [ "$INCLUDE_AUTOMATION" != "0" ] && [ "$INCLUDE_AUTOMATION" != "1" ]; then
  echo "INCLUDE_AUTOMATION must be 0 or 1." >&2
  exit 1
fi

if [ "$INCLUDE_POLICY" != "0" ] && [ "$INCLUDE_POLICY" != "1" ]; then
  echo "INCLUDE_POLICY must be 0 or 1." >&2
  exit 1
fi

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

if [ "$INCLUDE_AUTOMATION" = "1" ]; then
  WORKER_UP="$(
    query_value 'max(up{job="polygate-automation-worker"})' 2>/dev/null || echo 0
  )"
  WORKER_QUEUE_SERIES="$(
    query_value 'count(automation_worker_queue_depth)' 2>/dev/null || echo 0
  )"
  if python3 -c "raise SystemExit(0 if float('$WORKER_UP') == 1 else 1)" \
    && positive_number "$WORKER_QUEUE_SERIES"; then
    ok "Automation Worker target and queue metrics are present"
  else
    bad "Automation Worker target or queue metrics are missing"
  fi
fi

if [ "$INCLUDE_POLICY" = "1" ]; then
  POLICY_API_UP="$(
    query_value 'max(up{job="polygate-automation-api"})' 2>/dev/null || echo 0
  )"
  if python3 -c "raise SystemExit(0 if float('$POLICY_API_UP') == 1 else 1)"; then
    ok "Automation API (Policy control plane) target is UP"
  else
    bad "Automation API target is not UP"
  fi

  ACTIVE_SERIES="$(
    query_value 'count(polygate_policy_active_version)' 2>/dev/null || echo 0
  )"
  if positive_number "$ACTIVE_SERIES"; then
    ok "Policy control plane exports an active version"
  else
    bad "polygate_policy_active_version is absent"
  fi

  # Every Gateway Pod must report its own loaded version, so the series count
  # has to match the Gateway target count discovered above — one lagging Pod
  # would otherwise hide behind an aggregate.
  GATEWAY_LOADED="$(
    query_value 'count(polygate_policy_loaded_version{component="gateway"})' \
      2>/dev/null || echo 0
  )"
  if positive_number "$GATEWAY_TARGETS" \
    && python3 -c "raise SystemExit(0 if float('$GATEWAY_LOADED') == float('$GATEWAY_TARGETS') else 1)"; then
    ok "Every Gateway Pod reports a loaded policy version ($GATEWAY_LOADED/$GATEWAY_TARGETS)"
  else
    bad "Gateway loaded-policy series are incomplete ($GATEWAY_LOADED/$GATEWAY_TARGETS)"
  fi

  WORKER_LOADED="$(
    query_value 'count(polygate_policy_loaded_version{component="automation-worker"})' \
      2>/dev/null || echo 0
  )"
  if python3 -c "raise SystemExit(0 if float('$WORKER_LOADED') == 1 else 1)"; then
    ok "Automation Worker reports a loaded policy version"
  else
    bad "Worker loaded-policy series is missing ($WORKER_LOADED, expected 1)"
  fi

  DRIFT="$(
    query_value \
      'max(polygate_policy_active_version) - min(polygate_policy_loaded_version)' \
      2>/dev/null || echo 999
  )"
  if python3 -c "raise SystemExit(0 if float('$DRIFT') == 0 else 1)"; then
    ok "All components converged on the active policy version"
  else
    bad "Policy version drift is $DRIFT (expected 0)"
  fi
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
  if echo "$DASHBOARD" \
    | INCLUDE_AUTOMATION="$INCLUDE_AUTOMATION" INCLUDE_POLICY="$INCLUDE_POLICY" python3 -c '
import json
import os
import sys

dashboard = json.load(sys.stdin).get("dashboard", {})
panels = dashboard.get("panels", [])
expressions = [
    target.get("expr", "")
    for panel in panels
    for target in panel.get("targets", [])
]
titles = {panel.get("title") for panel in panels if panel.get("title")}
required = {
    "container_cpu_usage_seconds_total",
    "container_memory_working_set_bytes",
    "kube_deployment_status_replicas_available",
    "kube_horizontalpodautoscaler_status_desired_replicas",
}
required_titles = set()
minimum_panels = 20
if os.environ.get("INCLUDE_AUTOMATION") == "1":
    minimum_panels = 29
    required.update(
        {
            "automation_worker_in_flight",
            "automation_worker_job_duration_seconds_bucket",
            "automation_worker_jobs_failed_total",
            "automation_worker_jobs_processed_total",
            "automation_worker_jobs_retried_total",
            "automation_worker_queue_depth",
            "automation_worker_queue_wait_seconds_bucket",
        }
    )
if os.environ.get("INCLUDE_POLICY") == "1":
    minimum_panels = 37
    required.update(
        {
            "polygate_policy_active_version",
            "polygate_policy_last_publish_timestamp_seconds",
            "polygate_policy_loaded_version",
            "polygate_policy_publications_total",
            "polygate_policy_reload_failures_total",
        }
    )
    # 标题被计划固定，演示脚本与截图按标题定位。
    required_titles.update(
        {
            "Active Policy Version",
            "Gateway Loaded Policy",
            "Worker Loaded Policy",
            "Policy Publication Outcomes",
            "Policy Reload Failures",
            "Last Policy Publication",
            "Open Policy Editor",
        }
    )
valid = (
    len(panels) >= minimum_panels
    and all(
        any(metric in expression for expression in expressions)
        for metric in required
    )
    and required_titles <= titles
)
raise SystemExit(0 if valid else 1)
'; then
    ok "The protected Grafana dashboard contains required resource panels"
  else
    bad "The Grafana dashboard is missing or authentication failed"
  fi
fi

echo
echo "$PASS passed, $FAIL failed"

if [ "$FAIL" -gt 0 ]; then
  exit 1
fi
