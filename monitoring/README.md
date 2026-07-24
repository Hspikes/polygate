# Local monitoring

This directory contains the local monitoring stack. Prometheus periodically
reads the Gateway's `GET /metrics` endpoint. Grafana provides the full
operations dashboard, while Monitoring API turns fixed Prometheus queries into
stable JSON for small summaries and programmatic consumers.

```text
Gateway /metrics -> Prometheus -> Grafana dashboard
                              \-> Monitoring API
```

The local Compose stage covers Gateway business metrics. The same Grafana
dashboard also contains Kubernetes Pod CPU, memory, restart, and HPA panels;
they show `N/A` locally and receive data after the prepared Kubernetes
monitoring stack is deployed.

## Start

From the repository root:

```bash
docker compose config
docker compose up --build -d
```

The first run builds the Gateway/Mock images and pulls the pinned Redis and
Prometheus images. Compose also creates an isolated project network so
Prometheus can reach `gateway:8000`.

Useful endpoints:

- Gateway health: <http://localhost:8000/health>
- Gateway raw metrics: <http://localhost:8000/metrics>
- Prometheus UI: <http://localhost:9090>
- Prometheus targets: <http://localhost:9090/targets>
- Prometheus readiness: <http://localhost:9090/-/ready>
- Monitoring API health: <http://localhost:8010/health>
- Monitoring overview: <http://localhost:8010/api/monitoring/overview>
- Grafana dashboard: <http://localhost:3000/d/polygate-overview/polygate-overview>

Run the automated local checks after the stack starts:

```bash
./scripts/smoke-test.sh
./scripts/prometheus-smoke-test.sh
./scripts/monitoring-api-smoke-test.sh
./scripts/grafana-smoke-test.sh
```

They verify that Prometheus is ready, the Gateway target is `UP`, and both
Prometheus and Monitoring API observe new Gateway requests. The Grafana check
also verifies that the version-controlled data source and dashboard were
provisioned and that a query can travel through Grafana to Prometheus.

## Grafana dashboard

Grafana is pinned to the OSS `grafana/grafana:12.4.0` image. No manual setup is
needed: Compose mounts the data-source YAML and dashboard JSON from
`monitoring/grafana/`, and the dashboard opens as the local home page.

The dashboard includes Gateway availability, request throughput, service error
rate, client rejection rate, cancellation rate, P95 latency, cache hit rate,
tokens, estimated cost, and per-provider traffic, success rate, latency, and
cost. Its Kubernetes section adds available and desired replicas, per-Pod CPU
and memory, HPA history, and container restarts.

Anonymous Viewer access is enabled only to make local coursework demos open
without a login. Do not expose this Compose configuration directly to the
public internet. Dashboard edits should be made in the JSON file and committed
to Git; the provisioned dashboard is read-only in the UI.

## Monitoring API

The API accepts a fixed window rather than arbitrary PromQL:

```bash
curl "http://localhost:8010/api/monitoring/overview?window=15m"
```

Allowed windows are `5m`, `15m`, `1h`, and `6h`. The response contract is
defined in:

- `contracts/monitoring-overview.schema.json`
- `contracts/monitoring-overview.example.json`

The local response sets `resources.available` to `false`. Monitoring API is
kept as an optional local JSON consumer and is intentionally not deployed to
Kubernetes. The cloud Grafana dashboard reads resource metrics directly from
Prometheus; deployment details are in `deploy/monitoring/README.md`.

Ratio fields use `null` when the selected window contains no relevant traffic:

- `gateway.error_rate` is the service error rate. Its numerator includes only
  `routing_error`, `provider_error`, `provider_timeout`, `server_error`, and
  `partial_error`; its denominator excludes `client_error` and `cancelled`.
- `gateway.client_rejection_rate` and `gateway.cancellation_rate` divide their
  respective outcomes by all Gateway requests.
- Gateway ratio fields are `null` when their denominator has no traffic.
- `cache.hit_rate` is `null` when there are no cache lookups.
- `providers[].success_rate` excludes client-cancelled calls from its
  denominator and is `null` when that provider has no eligible calls.

This is different from `0`, which means traffic existed and none of it matched
the numerator (for example, a provider received calls but none succeeded).
Grafana renders unavailable ratios as `N/A`.

`partial` becomes `true` when Prometheus reports the Gateway scrape target as
down or cannot find that target. In that case, `warnings` explains that values
may be incomplete or stale. With a healthy target but no traffic, `partial`
remains `false` and `warnings` explains which rate-based fields are unavailable.
If Prometheus cannot execute the fixed queries at all, the endpoint still
returns HTTP 502 rather than a partial response.

## Recording rules and alerts

Both local and Kubernetes Prometheus load
`monitoring/prometheus/polygate-rules.yml`. The file records the 5-minute
service error, client rejection, and cancellation ratios and defines this
minimal alert set:

- `GatewayTargetDown`
- `HighProviderErrorOrTimeoutRate`
- `GatewayP95LatencyAboveSLO`
- `ProviderCircuitOpenTooLong`
- `GatewayPodRestarting` (Kubernetes only)
- `GatewayHPAAtMaxReplicas` (Kubernetes only)

Client rejection and cancellation never trigger the service-error alert.
Alert labels remain low-cardinality; request IDs, prompts, URLs, and raw error
messages are deliberately absent. Prometheus evaluates the rules locally even
without Alertmanager, so their pending/firing state is visible at
<http://localhost:9090/alerts>.

Run the API unit tests inside its Docker image; no host Python environment is
required:

```bash
docker compose run --rm monitoring-api \
  python -m unittest discover -s tests -v
```

## Useful starter queries

```promql
up{job="polygate-gateway"}
sum(polygate_requests_total)
sum by (outcome) (polygate_requests_total)
polygate:gateway_service_error_ratio:rate5m
polygate:gateway_client_rejection_ratio:rate5m
polygate:gateway_cancellation_ratio:rate5m
sum by (result) (polygate_cache_requests_total)
sum by (provider, outcome) (polygate_provider_requests_total)
polygate_circuit_state
sum by (provider, direction) (polygate_tokens_total)
sum(polygate_estimated_cost_usd_total)
```

## Stop and restart

```bash
docker compose down
docker compose up --build -d
```

The `prometheus-data` named volume preserves local samples across a normal
`docker compose down`. Running `docker compose down -v` also deletes that data
and should only be used when an intentional clean reset is needed.

## Troubleshooting

If the Prometheus target is `DOWN`:

1. Check `docker compose ps`.
2. Check Gateway health with `curl http://localhost:8000/health`.
3. Check logs with `docker compose logs gateway prometheus`.
4. Keep the target as `gateway:8000`, not `localhost:8000`: from inside the
   Prometheus container, `localhost` means the Prometheus container itself.
5. Monitoring API must use `http://prometheus:9090`, not `localhost:9090`, for
   the same container-network reason.
6. Grafana uses the same `http://prometheus:9090` Compose-network URL. Check its
   provisioning and query path with `./scripts/grafana-smoke-test.sh`.
