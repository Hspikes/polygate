# PolyGate

> **An AI API control plane for teams.** One endpoint for developers. One policy
> plane for the organization.

PolyGate is an OpenAI-compatible, policy-aware gateway for shared AI
infrastructure. It lifts routing and scheduling decisions out of individual
applications, applies one versioned organizational policy, and leaves an
explainable record of what happened on every request.

**Control decides. Execution enforces. Observability proves.**

| Web Console | Operations Dashboard |
|---|---|
| ![PolyGate Web Console](./assets/web-console.png) | ![PolyGate Grafana dashboard](./assets/observability-dashboard.png) |

## Why a control plane?

Giving every team direct access to AI providers looks simple: hand out API
keys, set a budget, and let people move fast. Then the rules arrive.

- Sensitive data must stay inside an approved boundary.
- Different workloads need different model capabilities and quality levels.
- Provider health, latency, and price change independently.
- Urgent work should move first without starving routine jobs.
- Every exception, override, retry, and fallback needs an owner and an audit
  trail.

Putting those decisions into every application creates policy drift. Putting a
thin proxy in front of providers only moves the same tangle into one process.
PolyGate takes its design cue from packet networks: separate the system that
**computes policy**, the path that **executes it**, and the telemetry that
**proves what actually happened**.

| Packet networks | PolyGate |
|---|---|
| Control plane computes routes | Control plane publishes versioned organizational policy |
| Data plane forwards packets | Execution plane schedules and routes AI work |
| Telemetry reports network behavior | Observability records decisions, cost, latency, and drift |

The result is not one more endpoint in front of many providers. It is routing
for AI work.

## One policy, two decisions

Every request creates two separate decisions:

1. **Who runs next?** The Automation worker orders queued jobs by urgency, then
   ages waiting work so lower-priority jobs cannot starve.
2. **Where should the work run?** The Gateway evaluates the same ordered route:
   privacy and capability are hard gates; health, budget, and latency filter the
   candidates; quality selects the winner.

The decision result includes the chosen provider, a human-readable reason,
estimated cost, latency, retries, failover state, and request ID. Queueing and
routing can change together when a new policy is published while the client
request remains unchanged.

## Architecture

```mermaid
flowchart LR
    Client[Web, OpenAI client, or agent] --> Gateway
    Gateway --> Providers[AI providers]
    Gateway <--> Cache[(Redis)]

    Admin[Administrator] --> Editor[Policy Editor]
    Editor --> Automation[Automation API]
    Automation --> Policy[(Versioned policy)]
    Automation <--> Queue[(Redis queue)]
    Queue --> Worker[Automation worker]
    Worker --> Gateway
    Policy -. hot reload .-> Gateway
    Policy -. hot reload .-> Worker

    Gateway --> Prometheus
    Automation --> Prometheus
    Worker --> Prometheus
    Prometheus --> Grafana

    classDef control fill:#e5f0ee,stroke:#11645d,color:#173b37;
    classDef execution fill:#eceef1,stroke:#596273,color:#252a33;
    classDef observe fill:#f7efe0,stroke:#c47a13,color:#6d4309;
    class Editor,Automation,Policy control;
    class Gateway,Worker,Providers,Queue,Cache execution;
    class Prometheus,Grafana observe;
```

- **Control plane:** policy validation, simulation, publication, rollback, and
  asynchronous intent compilation.
- **Execution plane:** an OpenAI-compatible Gateway, priority worker, provider
  adapters, cache, retries, circuit breakers, and pre-response failover.
- **Observability plane:** Prometheus metrics, Grafana dashboards, redacted
  Decision Records, and policy-version drift detection.

## What is implemented

This repository distinguishes working code from deployment assets and future
direction. It does not imply that a public cloud deployment is currently
running.

| Capability | Status | Evidence in the repository |
|---|---|---|
| Policy-aware Gateway | Implemented | OpenAI Chat Completions compatibility, provider registry, ordered routing gates, exact cache, and explainable decisions |
| Request reliability | Implemented | Bounded retries, circuit breakers, request-level time budgets, and failover before downstream output begins |
| Automation scheduling | Implemented | Intent/preview/job APIs, Redis priority queue, leases, retries, and anti-starvation aging |
| Policy control plane | Implemented | Validate → preview → publish lifecycle, version history, hot reload, Last Known Good fallback, and rollback |
| Observability | Implemented | Prometheus metrics, Grafana dashboards, Decision Records, and policy convergence metrics |
| Kubernetes / EKS | Deployment assets | Manifests, RBAC, ConfigMap persistence, HPA, preflight checks, and smoke tests are included |
| Web chat → Gateway | Integrated | Multi-turn chat, route preferences, streaming responses, and decision cards |
| Web automation cards and Pi automation path | Partial | Components exist; the complete end-user workflow is still being connected |
| Semantic cache, KEDA, provider CRDs, and multi-tenant billing | Roadmap | Planned extensions to the control-plane model |

## Quick start

The default stack uses deterministic mock providers, so it can be explored
without an external model key or API charges.

```bash
cp .env.example .env
docker compose up --build -d
docker compose ps
```

The following services should become healthy:

| Service | URL | Purpose |
|---|---|---|
| Web Console | <http://localhost:8080> | Multi-turn chat and decision cards |
| Gateway | <http://localhost:8000> | OpenAI-compatible API |
| Automation API | <http://localhost:8020/docs> | Templates, previews, jobs, and policy lifecycle |
| Policy Editor | <http://localhost:8020/admin/policies> | Private validate/preview/publish/rollback interface |
| Monitoring API | <http://localhost:8010/api/monitoring/overview> | Curated Prometheus queries as JSON |
| Prometheus | <http://localhost:9090/targets> | Metrics and target health |
| Grafana | <http://localhost:3000/d/polygate-overview/polygate-overview> | Request, cost, reliability, and policy dashboards |

Send a policy-aware request through the same OpenAI-compatible surface an
existing client would use:

```bash
curl http://localhost:8000/v1/chat/completions \
  -H 'Content-Type: application/json' \
  -d '{
    "model": "auto",
    "messages": [{"role": "user", "content": "Summarize this incident."}],
    "polygate": {
      "privacy": "standard",
      "quality": "balanced",
      "latency_target_ms": 3000,
      "max_cost_usd": 0.01
    }
  }'
```

The response carries the model answer and a `polygate` decision card. A
corresponding redacted Decision Record can be queried by request ID while its
Redis TTL is active.

To enable the real DeepSeek adapters, set `REAL_A_API_KEY` in `.env`. The
mock providers remain the recommended path for local development and automated
verification.

## Policy lifecycle

A policy change follows a deliberate release path:

```text
edit -> validate -> simulate/preview -> publish -> hot reload -> converge
                                                        \-> rollback
```

Automation is the only policy writer. Gateway and Worker read the policy,
validate the complete document, and atomically swap versions. If the control
plane is temporarily unavailable, the execution plane continues serving from
its Last Known Good policy.

## Verification gates

### Backend

Use Python 3.12. Worker tests require a real Redis instance on an isolated
database. Gateway cache tests also require Redis; check the skip count as well
as the failure count.

```bash
AUTOMATION_TEST_REDIS_URL=redis://127.0.0.1:6379/15 \
  python -m pytest automation/tests -q

cd gateway
python -m pytest tests -q
cd ..
```

### Web

```bash
cd web
npm test
npm run lint
npm run build
cd ..
```

The supported Web test runtime is Node 22. Node 25's experimental
`localStorage` conflicts with jsdom and is not part of the verified toolchain.

### Contracts, deployment, and behavior

```bash
python3 scripts/tests/test-automation-contracts.py
python3 scripts/tests/test-policy-contracts.py
bash scripts/tests/test-deployment-automation.sh
bash scripts/tests/test-deployment-policy.sh
./scripts/kubernetes-monitoring-preflight.sh

./scripts/web-smoke-test.sh
./scripts/kubernetes-automation-smoke-test.sh
./scripts/automation-peak-test.sh
```

See [Gateway](./gateway/README.md),
[Automation](./automation/README.md), and
[Kubernetes deployment](./deploy/README.md) for component-specific gates and
operational caveats.

## Repository map

| Path | Responsibility |
|---|---|
| `gateway/` | OpenAI-compatible API, routing, cache, reliability, and Decision Records |
| `providers/` | Real and fault-injectable mock provider adapters |
| `automation/` | Intent/preview/job APIs, worker scheduling, and policy lifecycle |
| `web/` | Chat console, route preferences, and decision cards |
| `agent/`, `.pi/extensions/` | Agent boundary and Pi integration |
| `contracts/` | Cross-component JSON Schemas, examples, policy, and provider registry |
| `deploy/`, `monitoring/` | Compose, Kubernetes/EKS, Prometheus, and Grafana assets |
| `scripts/` | Contract, deployment, smoke, and load verification |

Component documentation:

- [Gateway](./gateway/README.md) · [Providers](./providers/README.md)
- [Automation Service](./automation/README.md) · [Contract Registry](./contracts/README.md)
- [Web Console](./web/README.md) · [Agent and Pi integration](./agent/README.md)
- [Kubernetes deployment](./deploy/README.md)
- [Local monitoring](./monitoring/README.md) · [Kubernetes monitoring](./deploy/monitoring/README.md)

## Security and operational boundaries

- `contracts/` is the source of truth for cross-service interfaces. Breaking
  changes must update implementations, examples, and contract tests together.
- Never commit `.env`, cloud credentials, provider API keys, Grafana passwords,
  or real user prompts.
- `privacy=high` requests must not route to providers marked `external`.
- Policy Editor and monitoring surfaces are administrative tools; deployment
  manifests keep them off the public application entry point.
- Decision Records are redacted, authenticated when Gateway auth is enabled,
  and expire from Redis. Prompts, tool arguments, credentials, upstream URLs,
  and raw errors are not stored in them.
- Circuit-breaker state is currently local to each Gateway replica rather than
  shared across replicas.

## From a control plane to the AI service network

PolyGate begins inside one organization, but its architecture does not stop at
the organizational boundary.

Once policy is versioned, execution is decoupled, and every decision carries
evidence, control planes can become interoperable. Organizations can publish
constraints instead of provider-specific code. Providers and in-house clusters
can advertise capability, locality, cost, and health. Shared exchange points
can match AI work to the right execution domain while preserving each
participant's policy.

What starts as an internal gateway becomes a routing node. What starts as one
policy plane becomes a fabric of cooperating control planes.

**PolyGate lays the control, execution, and observability foundations for that
AI API service network.**

## License

PolyGate is licensed under the [Apache License 2.0](./LICENSE). Contributions
are accepted under the same license. Copyright 2026 PolyGate contributors.
