# Local monitoring

This directory contains the local monitoring backend: Prometheus periodically
reads the Gateway's `GET /metrics` endpoint, and Monitoring API turns fixed
Prometheus queries into stable frontend-facing JSON.

```text
Gateway /metrics -> Prometheus -> Monitoring API
```

This stage covers Gateway business metrics only. Grafana, the monitoring
frontend, Kubernetes Pod metrics, and HPA data are separate later stages.

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

Run both automated local checks after the stack starts:

```bash
./scripts/prometheus-smoke-test.sh
./scripts/monitoring-api-smoke-test.sh
```

They verify that Prometheus is ready, the Gateway target is `UP`, and both
Prometheus and Monitoring API observe new Gateway requests.

## Monitoring API

The API accepts a fixed window rather than arbitrary PromQL:

```bash
curl "http://localhost:8010/api/monitoring/overview?window=15m"
```

Allowed windows are `5m`, `15m`, `1h`, and `6h`. The response contract is
defined in:

- `contracts/monitoring-overview.schema.json`
- `contracts/monitoring-overview.example.json`

The local response sets `resources.available` to `false`. CPU, memory, and HPA
replicas will be filled in later when Kubernetes resource metrics are connected.

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
sum by (result) (polygate_cache_requests_total)
sum by (provider, outcome) (polygate_provider_requests_total)
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
