# PolyGate Automation Service

This standalone FastAPI control plane owns intent compilation, asynchronous
Automation jobs, and the versioned Policy v1 lifecycle. It deliberately does
not run inside the Gateway process.

## HTTP surfaces

- `GET /health`
- `GET /ready`
- `GET /metrics`
- `GET /v1/templates`
- `POST /v1/requests/preview`
- `POST /v1/jobs` with `Idempotency-Key`
- `GET /v1/jobs?status=queued`
- `GET /v1/jobs/{job_id}`
- `GET /v1/policies/active`
- `GET /v1/admin/policies`
- `GET /v1/admin/policies/{version}`
- `POST /v1/admin/policies/validate`
- `POST /v1/admin/policies/preview`
- `POST /v1/admin/policies/publish`
- `POST /v1/admin/policies/{version}/rollback`

The private Policy Editor is available at `GET /admin/policies`. It is served
from the same Automation image and origin as the Policy API, with a strict CSP
and `Cache-Control: no-store`. Its Alpine CSP runtime is pinned and vendored
under `automation/admin/vendor`; the page has no Node build, CDN, public
ingress, or browser-side draft persistence.

The editor requires the release order **Validate → Preview → Publish**. Editing
any policy field invalidates earlier validation and preview results. The
administrator key is entered manually, retained only in the page's private
in-memory JavaScript closure, and cleared on refresh, disconnect, or a 401.

## Run

```bash
docker build -f automation/Dockerfile -t polygate-automation:dev .
docker run --rm -p 8020:8020 polygate-automation:dev
```

Open <http://localhost:8020/docs>, call `GET /health`, or open the private editor
at <http://localhost:8020/admin/policies>.

For the full local stack, use Compose from the repository root:

```bash
docker compose up -d --build automation
```

The local administrator key is configured by Compose for development and must
still be entered manually in the page. Compose uses an in-memory Policy
Repository, so published history returns to the mounted baseline after the
Automation container restarts.

## Integration gate

The full integration sequence that must pass before an EKS deploy lives in the
root [README](../README.md#verification-gates). The Automation-specific
parts of it:

```bash
# Needs a real Redis on db 15 for Worker tests and Python 3.12.
AUTOMATION_TEST_REDIS_URL=redis://127.0.0.1:6379/15 python -m pytest automation/tests -q

# Preview/Job policy_version stamping, idempotency, Worker completion, metrics.
./scripts/kubernetes-automation-smoke-test.sh

# Priority scheduling: submitted low -> critical, must execute critical -> low.
./scripts/automation-peak-test.sh
```

`automation-peak-test.sh` submits four intents in reverse priority order and then
prints the order they were actually claimed in, sorted by `started_at`. Seeing
`critical` first is the evidence that `effective_priority` scheduling works; the
same run also asserts every job keeps one consistent `policy_version` from
preview through completion.

## Policy Editor verification

Build the exact image and run the Automation suite in Python 3.12:

```bash
docker build --platform linux/amd64 -f automation/Dockerfile \
  -t polygate-automation:policy-ui .
docker run --rm -v "$PWD":/src -w /src polygate-automation:policy-ui \
  sh -c 'pip install -q pytest; python -m pytest automation/tests/ -q'
```

For EKS, keep `service/automation` as `ClusterIP` and access the same page with:

```bash
kubectl port-forward service/automation 8020:8020
```
