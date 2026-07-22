# PolyGate Automation Service

This standalone FastAPI control plane freezes the cross-team boundary for
intent templates, request previews, and asynchronous jobs. It deliberately
does not run inside the Gateway process.

## Current skeleton

- `GET /health`
- `GET /v1/templates`
- `POST /v1/requests/preview`
- `POST /v1/jobs` with `Idempotency-Key`
- `GET /v1/jobs?status=queued`
- `GET /v1/jobs/{job_id}`

Preview compilation and policy locking are functional. Preview and Job data
are currently process-local so A and D can integrate immediately. B owns the
replacement Redis repository and Scheduler Worker; its implementation must
preserve the version-controlled schemas under `contracts/`.

## Run

```bash
docker build -f automation/Dockerfile -t polygate-automation:dev .
docker run --rm -p 8020:8020 polygate-automation:dev
```

Open <http://localhost:8020/docs> or call `GET /health`.

## Ownership seams

- A: templates, preview compiler, API validation, Gateway client.
- B: Redis repository, priority scheduler, worker, leases, retries.
- D: Pi extension and Chat UI consume this API only.
- C: image build, Compose/EKS, Prometheus/Grafana, smoke tests.
