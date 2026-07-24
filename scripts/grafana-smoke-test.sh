#!/usr/bin/env bash
# Verify local Prometheus -> Grafana provisioning and query flow.
#
# Run after:
#   docker compose up --build -d

set -uo pipefail

GRAFANA="${GRAFANA:-http://localhost:3000}"
MONITORING_API="${MONITORING_API:-http://localhost:8010}"

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

echo "PolyGate Grafana smoke test"
echo "Grafana:        $GRAFANA"
echo "Monitoring API: $MONITORING_API"

READY=false
for _ in $(seq 1 20); do
  if curl -fsS "$GRAFANA/api/health" >/dev/null 2>&1; then
    READY=true
    break
  fi
  sleep 2
done

if [ "$READY" = true ]; then
  ok "Grafana is ready"
else
  bad "Grafana did not become ready within 40 seconds"
  echo
  echo "$PASS passed, $FAIL failed"
  exit 1
fi

HEALTH="$(curl -fsS "$GRAFANA/api/health" 2>/dev/null || echo '{}')"
if echo "$HEALTH" | python3 -c '
import json
import sys

body = json.load(sys.stdin)
raise SystemExit(0 if body.get("database") == "ok" else 1)
'; then
  ok "Grafana database health is OK"
else
  bad "Grafana returned an unexpected health response"
fi

DATASOURCE="$(curl -fsS "$GRAFANA/api/datasources/uid/prometheus" 2>/dev/null || echo '{}')"
if echo "$DATASOURCE" | python3 -c '
import json
import sys

body = json.load(sys.stdin)
expected = (
    body.get("uid") == "prometheus"
    and body.get("type") == "prometheus"
    and body.get("url") == "http://prometheus:9090"
    and body.get("isDefault") is True
    and body.get("readOnly") is True
)
raise SystemExit(0 if expected else 1)
'; then
  ok "Provisioned Prometheus data source uses the Compose service URL"
else
  bad "Prometheus data source is missing or incorrectly configured"
fi

DATASOURCE_HEALTH="$(
  curl -fsS "$GRAFANA/api/datasources/uid/prometheus/health" 2>/dev/null \
    || echo '{}'
)"
if echo "$DATASOURCE_HEALTH" | python3 -c '
import json
import sys

body = json.load(sys.stdin)
raise SystemExit(0 if body.get("status") == "OK" else 1)
'; then
  ok "Grafana can reach the Prometheus backend"
else
  bad "Grafana cannot query the Prometheus backend"
fi

DASHBOARD="$(
  curl -fsS "$GRAFANA/api/dashboards/uid/polygate-overview" 2>/dev/null \
    || echo '{}'
)"
if echo "$DASHBOARD" | python3 -c '
import json
import sys

body = json.load(sys.stdin)
dashboard = body.get("dashboard", {})
panels = dashboard.get("panels", [])
expressions = [
    target.get("expr", "")
    for panel in panels
    for target in panel.get("targets", [])
]
expected = (
    dashboard.get("uid") == "polygate-overview"
    and dashboard.get("title") == "PolyGate Overview"
    and len(panels) >= 20
    and any("polygate_requests_total" in expr for expr in expressions)
    and any("polygate_provider_requests_total" in expr for expr in expressions)
    and any("provider_timeout" in expr for expr in expressions)
    and any("client_error" in expr for expr in expressions)
    and any("cancelled" in expr for expr in expressions)
    and all(".*_error" not in expr for expr in expressions)
    and all("clamp_min" not in expr for expr in expressions)
    and any("container_cpu_usage_seconds_total" in expr for expr in expressions)
    and any("container_memory_working_set_bytes" in expr for expr in expressions)
    and any("kube_deployment_status_replicas_available" in expr for expr in expressions)
    and any("kube_horizontalpodautoscaler_status_desired_replicas" in expr for expr in expressions)
)
raise SystemExit(0 if expected else 1)
'; then
  ok "PolyGate business and Kubernetes dashboard panels are provisioned"
else
  bad "PolyGate dashboard is missing or incomplete"
fi

NOW_MS=$(( $(date +%s) * 1000 ))
FROM_MS=$(( NOW_MS - 300000 ))
QUERY_BODY="{\"from\":\"$FROM_MS\",\"to\":\"$NOW_MS\",\"queries\":[{\"refId\":\"A\",\"datasource\":{\"type\":\"prometheus\",\"uid\":\"prometheus\"},\"expr\":\"up{job=\\\"polygate-gateway\\\"}\",\"instant\":true,\"range\":false}]}"
QUERY_RESULT="$(
  curl -fsS -X POST "$GRAFANA/api/ds/query" \
    -H "Content-Type: application/json" \
    -d "$QUERY_BODY" 2>/dev/null \
    || echo '{}'
)"
if echo "$QUERY_RESULT" | python3 -c '
import json
import sys

body = json.load(sys.stdin)
frames = body.get("results", {}).get("A", {}).get("frames", [])
values = [
    value
    for frame in frames
    for field_values in frame.get("data", {}).get("values", [])
    for value in field_values
]
raise SystemExit(0 if 1 in values or 1.0 in values else 1)
'; then
  ok "A query through Grafana reports the Gateway target as UP"
else
  bad "Grafana query path did not report the Gateway target as UP"
fi

BACKEND_HEALTH="$(
  curl -fsS "$MONITORING_API/health" 2>/dev/null \
    || echo '{}'
)"
if echo "$BACKEND_HEALTH" | python3 -c '
import json
import sys

body = json.load(sys.stdin)
expected = (
    body.get("status") == "ok"
    and body.get("prometheus_reachable") is True
)
raise SystemExit(0 if expected else 1)
'; then
  ok "Monitoring API remains connected to Prometheus"
else
  bad "Monitoring API backend health is degraded"
fi

echo
echo "$PASS passed, $FAIL failed"

if [ "$FAIL" -gt 0 ]; then
  exit 1
fi
