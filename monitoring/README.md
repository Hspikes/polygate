# Local monitoring

This directory contains the first monitoring stage: a local Prometheus server
that periodically reads the Gateway's `GET /metrics` endpoint.

```text
Gateway /metrics -> Prometheus -> Prometheus query UI/API
```

This stage covers Gateway business metrics only. Grafana, Monitoring API,
Kubernetes Pod metrics, and HPA data are separate later stages.

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

Run the automated local check after the stack starts:

```bash
./scripts/prometheus-smoke-test.sh
```

It verifies that Prometheus is ready, the Gateway target is `UP`, and a new
Gateway request causes `polygate_requests_total` to increase after the next
scrape.

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
