# PolyGate Administrator Policy Management Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a private, versioned Policy Editor that lets administrators safely preview, publish, observe, and roll back Gateway routing and Automation queue parameters without rebuilding images or restarting Gateway/Worker Pods.

**Architecture:** Automation remains the single Policy Control Plane and persists the active policy plus 20 versions in one Kubernetes ConfigMap, with Redis DB 0 used only as a rebuildable cache. Gateway and Worker load the mounted ConfigMap at startup, poll Automation every 5 seconds, validate before atomically swapping policy, and retain Last Known Good on failure. Grafana remains read-only and links to the private editor served by Automation.

**Tech Stack:** Python 3.12, FastAPI 0.115, Pydantic 2.9, httpx 0.27, Redis 7, React-free static HTML/CSS/JavaScript editor, Kubernetes 1.34 ConfigMap/RBAC/Secret, Prometheus 3.13, Grafana 12.4, Docker `linux/amd64`, AWS EKS/ECR `us-east-1`.

## Global Constraints

- `privacy=high` must always exclude external Providers.
- `finance_summary` must always normalize to `privacy=high`.
- Provider capability matching and unknown-provider rejection remain hard guardrails.
- Policy Admin stays private behind `service/automation`; no NodePort, LoadBalancer, iframe, or Grafana App Plugin.
- Admin writes require `Authorization: Bearer $POLICY_ADMIN_KEY`; the key is never logged or persisted by the browser.
- Kubernetes production wiring accepts the admin key only from `POLICY_ADMIN_KEY_FILE`. A plaintext `POLICY_ADMIN_KEY` fallback is allowed only when `POLICY_ALLOW_ENV_ADMIN_KEY=true`, which is reserved for local Compose.
- ConfigMap `polygate-routing-policy` is the durable source; Redis is cache only.
- Deployment may create the ConfigMap when absent but must never overwrite an existing ConfigMap.
- Runtime clients poll every 5 seconds and retain Last Known Good after any fetch or validation failure.
- ConfigMap history retains the active version and at most 19 additional versions.
- Existing queued Jobs keep their submission-time `initial_score` and `policy_version`; only new Jobs use new scoring.
- Queue runtime parameters (`waiting_bonus`, streak, starvation) may change immediately.
- Gateway cache keys include `policy_version`.
- Decision Card v1 remains unchanged; expose policy version through `X-PolyGate-Policy-Version`.
- Worker remains one replica with `WORKER_CONCURRENCY=1`.
- Redis remains DB 0 and all policy cache keys use `polygate:policy:` prefix.
- All production images target `linux/amd64`.
- Never stage `.pi/extensions/polygate-routing` or the two unrelated 2026-07-20 P1 plan files.

---

## Dependency and Merge Order

```text
Task 1 shared contracts (merge first)
  ├── Task 2 B policy models/lifecycle core
  │     ├── Task 3 B Kubernetes repository + Policy API
  │     └── Task 4 B Worker dynamic queue policy
  ├── Task 5 A Gateway runtime policy client/router
  │     └── Task 6 A simulation/cache/header/metrics
  ├── Task 7 D private Policy Editor
  └── Task 8 C Kubernetes/RBAC/bootstrap skeleton

Tasks 3, 4, 6, 7, 8 merged
  ├── Task 9 C Prometheus/Grafana
  ├── Task 10 local integration and regression gate
  └── Task 11 EKS deployment, smoke, recovery, evidence
```

Task 1 is the contract freeze gate. A/B/C/D must not merge implementation that invents different field or metric names.

---

### Task 1: Freeze Policy Contracts and Examples

**Owner:** A leads; B/C/D review before merge.

**Files:**
- Create: `contracts/policy.schema.json`
- Create: `contracts/policy-store.schema.json`
- Create: `contracts/policy-examples.json`
- Create: `scripts/tests/test-policy-contracts.py`
- Modify: `contracts/automation-preview.schema.json`
- Modify: `contracts/automation-job.schema.json`
- Modify: `contracts/automation-examples.json`
- Modify: `contracts/README.md`

**Interfaces:**
- Produces: JSON field names used by every later task.
- Produces: optional integer `policy_version` in Automation Preview and Job.
- Produces: metric names frozen in `contracts/README.md`.
- Does not change: `contracts/decision-card.schema.json`.

- [ ] **Step 1: Write the failing standard-library contract test**

Create `scripts/tests/test-policy-contracts.py` with tests that load all three new files and assert:

```python
SCHEMA_FILES = {
    "policy": CONTRACTS / "policy.schema.json",
    "store": CONTRACTS / "policy-store.schema.json",
}
EXAMPLES_FILE = CONTRACTS / "policy-examples.json"

EXPECTED_GATEWAY_FIELDS = {
    "assumed_output_tokens",
    "balanced_price_tolerance",
    "budget_mode",
    "latency_mode",
    "high_quality_strategy",
}
EXPECTED_METRICS = {
    "polygate_policy_active_version",
    "polygate_policy_loaded_version",
    "polygate_policy_publications_total",
    "polygate_policy_reload_failures_total",
    "polygate_policy_last_publish_timestamp_seconds",
}
```

The test must assert:

```python
self.assertEqual(policy["properties"]["schema_version"]["const"], 1)
self.assertFalse(policy["additionalProperties"])
self.assertEqual(
    set(policy["properties"]["gateway"]["properties"]),
    EXPECTED_GATEWAY_FIELDS,
)
self.assertEqual(
    policy["properties"]["automation"]["properties"]["urgency_scores"]
        ["additionalProperties"],
    False,
)
self.assertEqual(store["properties"]["versions"]["maxItems"], 20)
self.assertIn("policy_version", preview_schema["properties"])
self.assertIn("policy_version", job_schema["properties"])
self.assertTrue(EXPECTED_METRICS.issubset(set(readme_text.split())))
```

- [ ] **Step 2: Run the contract test and verify it fails because files are absent**

Run:

```bash
python3 scripts/tests/test-policy-contracts.py
```

Expected: failure opening `contracts/policy.schema.json`.

- [ ] **Step 3: Add the strict PolicyDraft schema**

`contracts/policy.schema.json` must use draft-07, reject unknown fields at every object level, and encode:

```json
{
  "schema_version": 1,
  "gateway": {
    "assumed_output_tokens": 256,
    "balanced_price_tolerance": 0.2,
    "budget_mode": "soft",
    "latency_mode": "soft",
    "high_quality_strategy": "prefer_real"
  },
  "automation": {
    "urgency_scores": {
      "critical": 100,
      "high": 60,
      "normal": 30,
      "low": 10
    },
    "scenarios": {
      "production_incident": {
        "weight": 40,
        "defaults": {
          "quality": "high",
          "privacy": "high",
          "max_cost_usd": 0.01,
          "latency_target_ms": 1000
        }
      },
      "customer_escalation": {
        "weight": 25,
        "defaults": {
          "quality": "balanced",
          "privacy": "standard",
          "max_cost_usd": 0.01,
          "latency_target_ms": 1500
        }
      },
      "finance_summary": {
        "weight": 15,
        "defaults": {
          "quality": "balanced",
          "privacy": "high",
          "max_cost_usd": 0.005,
          "latency_target_ms": 3000
        }
      },
      "marketing_batch": {
        "weight": 0,
        "defaults": {
          "quality": "cheap",
          "privacy": "standard",
          "max_cost_usd": 0.002,
          "latency_target_ms": 5000
        }
      }
    },
    "queue": {
      "waiting_bonus_interval_seconds": 5,
      "waiting_bonus_points": 1,
      "waiting_bonus_cap": 30,
      "starvation_streak_threshold": 3,
      "starvation_wait_seconds": 20
    }
  }
}
```

Use these schema constraints:

```text
assumed_output_tokens: integer 1..32768
balanced_price_tolerance: number 0..2
budget_mode: soft|hard
latency_mode: soft|hard
high_quality_strategy: prefer_real|lowest_cost
urgency scores: integer 0..1000
scenario weight: integer 0..500
max_cost_usd: number 0..10
latency_target_ms: integer 1..120000
waiting_bonus_interval_seconds: integer 1..3600
waiting_bonus_points: integer 0..100
waiting_bonus_cap: integer 0..1000
starvation_streak_threshold: integer 1..100
starvation_wait_seconds: integer 1..86400
```

- [ ] **Step 4: Add the version-store schema and examples**

`contracts/policy-store.schema.json` requires:

```json
{
  "active_version": 1,
  "versions": [
    {
      "version": 1,
      "status": "active",
      "created_at": "2026-07-24T00:00:00Z",
      "created_by": "bootstrap",
      "change_note": "Initial policy",
      "rollback_from": null,
      "policy": {}
    }
  ]
}
```

Rules:

- `active_version` is integer >= 1.
- `versions` has `minItems: 1`, `maxItems: 20`.
- `status` is `active` or `archived`.
- `rollback_from` is integer >= 1 or null.
- nested `policy` references `policy.schema.json`.

`contracts/policy-examples.json` contains exact examples under keys:

```text
draft
store
validate_response
preview_response
publish_request
publish_response
rollback_request
```

- [ ] **Step 5: Add backward-compatible Automation policy version fields**

Add optional:

```json
"policy_version": { "type": "integer", "minimum": 1 }
```

to Preview and Job schemas and examples. Do not add it to each schema's `required` list, so existing clients remain compatible.

- [ ] **Step 6: Document ownership and frozen metrics**

Add contract rows 11–13 to `contracts/README.md` for:

```text
policy.schema.json
policy-store.schema.json
policy-examples.json
```

Document the five exact metric names and update the cache formula to:

```text
sha256(normalize(messages) + privacy + scope + quality
       + max_cost_usd + latency_target_ms + policy_version)
```

- [ ] **Step 7: Run contract suites**

Run:

```bash
python3 scripts/tests/test-policy-contracts.py
python3 scripts/tests/test-automation-contracts.py
```

Expected: both suites end in `OK`.

- [ ] **Step 8: Commit and open the contract PR**

```bash
git add \
  contracts/policy.schema.json \
  contracts/policy-store.schema.json \
  contracts/policy-examples.json \
  contracts/automation-preview.schema.json \
  contracts/automation-job.schema.json \
  contracts/automation-examples.json \
  contracts/README.md \
  scripts/tests/test-policy-contracts.py
git commit -m "feat(contracts): define versioned policy management API"
git push -u origin feat/policy-contracts
```

PR title:

```text
feat(contracts): freeze policy management v1
```

Merge only after A/B/C/D acknowledge exact field and metric names.

---

### Task 2: Build Automation Policy Models and Lifecycle Core

**Owner:** B

**Files:**
- Create: `automation/app/policy_models.py`
- Create: `automation/app/policy_repository.py`
- Create: `automation/app/policy_manager.py`
- Create: `automation/tests/test_policy_models.py`
- Create: `automation/tests/test_policy_manager.py`
- Modify: `automation/app/models.py`
- Modify: `automation/app/main.py`
- Modify: `automation/tests/test_contract_alignment.py`

**Interfaces:**
- Consumes: Task 1 JSON names and examples.
- Produces: `PolicyDraft`, `PolicyVersion`, `PolicyStoreDocument`, `ActivePolicyResponse`.
- Produces: `PolicyRepository.load()` and `PolicyRepository.compare_and_swap()`.
- Produces: `PolicyManager.active`, `validate`, `preview_priority`, `publish`, `rollback`.

- [ ] **Step 1: Write failing model-alignment tests**

`automation/tests/test_policy_models.py` must load `contracts/policy-examples.json` and assert:

```python
draft = PolicyDraft.model_validate(EXAMPLES["draft"])
store = PolicyStoreDocument.model_validate(EXAMPLES["store"])

assert draft.gateway.budget_mode == "soft"
assert draft.automation.urgency_scores.critical == 100
assert store.active.version == 1
```

Also assert failures:

```python
with pytest.raises(ValidationError):
    PolicyDraft.model_validate({**payload, "unknown": True})

with pytest.raises(ValidationError):
    PolicyDraft.model_validate(policy_with_finance_privacy_standard)

with pytest.raises(ValidationError):
    PolicyDraft.model_validate(policy_with_scores_critical_below_high)
```

- [ ] **Step 2: Run and verify missing model failure**

Run:

```bash
python -m pytest automation/tests/test_policy_models.py -v
```

Expected: import failure for `automation.app.policy_models`.

- [ ] **Step 3: Implement strict Pydantic policy models**

Define in `automation/app/policy_models.py`:

```python
class GatewayPolicy(StrictModel):
    assumed_output_tokens: int = Field(ge=1, le=32768)
    balanced_price_tolerance: float = Field(ge=0, le=2)
    budget_mode: Literal["soft", "hard"]
    latency_mode: Literal["soft", "hard"]
    high_quality_strategy: Literal["prefer_real", "lowest_cost"]


class UrgencyScores(StrictModel):
    critical: int = Field(ge=0, le=1000)
    high: int = Field(ge=0, le=1000)
    normal: int = Field(ge=0, le=1000)
    low: int = Field(ge=0, le=1000)

    @model_validator(mode="after")
    def ordered(self):
        if not self.critical > self.high > self.normal > self.low:
            raise ValueError("urgency scores must satisfy critical > high > normal > low")
        return self


class QueuePolicy(StrictModel):
    waiting_bonus_interval_seconds: int = Field(ge=1, le=3600)
    waiting_bonus_points: int = Field(ge=0, le=100)
    waiting_bonus_cap: int = Field(ge=0, le=1000)
    starvation_streak_threshold: int = Field(ge=1, le=100)
    starvation_wait_seconds: int = Field(ge=1, le=86400)
```

Define scenario defaults using the existing `Preferences` model. Add a model validator that requires `finance_summary.defaults.privacy == "high"`.

Define:

```python
class PolicyVersion(StrictModel):
    version: int = Field(ge=1)
    status: Literal["active", "archived"]
    created_at: datetime
    created_by: str
    change_note: str = Field(min_length=1, max_length=500)
    rollback_from: int | None = Field(default=None, ge=1)
    policy: PolicyDraft


class ActivePolicyResponse(StrictModel):
    version: int = Field(ge=1)
    schema_version: Literal[1]
    published_at: datetime
    policy: PolicyDraft


class PolicyStoreDocument(StrictModel):
    active_version: int = Field(ge=1)
    versions: list[PolicyVersion] = Field(min_length=1, max_length=20)

    @property
    def active(self) -> PolicyVersion:
        matches = [v for v in self.versions if v.version == self.active_version]
        if len(matches) != 1:
            raise ValueError("active_version must reference exactly one version")
        return matches[0]
```

- [ ] **Step 4: Run model tests**

Run:

```bash
python -m pytest automation/tests/test_policy_models.py -v
```

Expected: all tests pass.

- [ ] **Step 5: Write failing PolicyManager lifecycle tests**

Create an in-memory repository fixture with revision strings. Test:

```python
manager = PolicyManager(repository=repository, cache=cache)
assert manager.active.version == 1

result = manager.publish(
    base_version=1,
    draft=changed_draft,
    change_note="Change queue weights",
    actor="policy-admin",
)
assert result.version == 2
assert repository.load().document.active_version == 2
```

Cover:

- stale base version raises `PolicyConflict`;
- version list truncates to 20;
- rollback from v2 creates v22 after 21 publishes;
- ConfigMap/repository failure does not change `manager.active`;
- cache failure returns warning but active version changes;
- active policy is immutable to callers.

- [ ] **Step 6: Implement repository protocol and in-memory fake**

In `automation/app/policy_repository.py`:

```python
@dataclass(frozen=True)
class RepositorySnapshot:
    document: PolicyStoreDocument
    revision: str


class PolicyRepository(Protocol):
    def load(self) -> RepositorySnapshot: ...

    def compare_and_swap(
        self,
        document: PolicyStoreDocument,
        expected_revision: str,
    ) -> RepositorySnapshot: ...
```

Implement `InMemoryPolicyRepository` with a lock and monotonically increasing revision for tests and local development.

- [ ] **Step 7: Implement PolicyManager**

`PolicyManager` must:

```python
class PolicyManager:
    @property
    def active(self) -> PolicyVersion: ...

    def validate(self, draft: PolicyDraft) -> list[str]: ...

    def preview_priority(
        self,
        draft: PolicyDraft,
        intents: list[AutomationIntent],
    ) -> list[PrioritySimulation]: ...

    def publish(
        self,
        *,
        base_version: int,
        draft: PolicyDraft,
        change_note: str,
        actor: str,
    ) -> PublishResult: ...

    def rollback(
        self,
        *,
        target_version: int,
        base_version: int,
        change_note: str,
        actor: str,
    ) -> PublishResult: ...
```

The manager writes durable repository state before swapping `_active`. Cache update occurs after `_active` swap and may add `"policy cache degraded"` to `warnings`.

- [ ] **Step 8: Make Automation preview use an injected policy**

Change:

```python
def _compile_preview(
    intent: AutomationIntent,
    policy_version: PolicyVersion,
) -> PreviewResponse:
```

Use:

```python
urgency_score = policy_version.policy.automation.urgency_scores.model_dump()[
    normalized.urgency.value
]
scenario_policy = policy_version.policy.automation.scenarios.model_dump()[
    normalized.scenario.value
]
score = urgency_score + scenario_policy["weight"]
```

Set `PreviewResponse.policy_version = policy_version.version`. Keep Finance privacy normalization as an immutable guardrail.

Update `create_app` to accept:

```python
def create_app(
    store: AutomationStore | None = None,
    policy_manager: PolicyManager | None = None,
    gateway_simulator: GatewaySimulator | None = None,
) -> FastAPI:
```

Tests use an in-memory bootstrap manager; production wiring remains for Task 3.

- [ ] **Step 9: Run Automation tests**

Run:

```bash
python -m pytest automation/tests/test_policy_models.py -v
python -m pytest automation/tests/test_policy_manager.py -v
python -m pytest automation/tests/test_api.py -v
python -m pytest automation/tests/test_contract_alignment.py -v
```

Expected: all pass.

- [ ] **Step 10: Commit**

```bash
git add automation/app automation/tests
git commit -m "feat(automation): add versioned policy lifecycle core"
```

---

### Task 3: Add Kubernetes ConfigMap Repository and Policy Admin API

**Owner:** B

**Files:**
- Create: `automation/app/kubernetes_policy_repository.py`
- Create: `automation/app/policy_auth.py`
- Create: `automation/app/policy_metrics.py`
- Create: `automation/tests/test_kubernetes_policy_repository.py`
- Create: `automation/tests/test_policy_api.py`
- Modify: `automation/app/main.py`
- Modify: `automation/app/policy_manager.py`
- Modify: `automation/Dockerfile`

**Interfaces:**
- Consumes: Task 2 models and manager.
- Consumes: Gateway `POST /internal/routing/simulate` response from Task 6; tests use a fake.
- Produces: all `/v1/policies` and `/v1/admin/policies` endpoints.
- Produces: `/metrics` on Automation port 8020.

- [ ] **Step 1: Write failing ConfigMap repository tests**

Use `httpx.MockTransport` to assert the repository calls:

```text
GET /api/v1/namespaces/default/configmaps/polygate-routing-policy
PUT /api/v1/namespaces/default/configmaps/polygate-routing-policy
```

The PUT body must contain:

```json
{
  "metadata": {
    "name": "polygate-routing-policy",
    "namespace": "default",
    "resourceVersion": "17"
  },
  "data": {
    "policy-store.json": "serialized PolicyStoreDocument"
  }
}
```

Test 409 maps to `RepositoryConflict`, 403 maps to `RepositoryUnavailable`, and malformed JSON is rejected.

- [ ] **Step 2: Run and verify missing repository failure**

```bash
python -m pytest automation/tests/test_kubernetes_policy_repository.py -v
```

Expected: import failure.

- [ ] **Step 3: Implement the least-privilege ConfigMap repository**

Read configuration:

```text
POD_NAMESPACE=default
POLICY_CONFIGMAP_NAME=polygate-routing-policy
POLICY_CONFIGMAP_KEY=policy-store.json
KUBERNETES_SERVICE_HOST
KUBERNETES_SERVICE_PORT_HTTPS
```

Read token and CA from:

```text
/var/run/secrets/kubernetes.io/serviceaccount/token
/var/run/secrets/kubernetes.io/serviceaccount/ca.crt
```

Use `httpx.Client(verify=ca_path, timeout=5.0)` and never log token, ConfigMap body, or policy change note.

- [ ] **Step 4: Write failing admin authentication/API tests**

Test exact behavior:

```text
GET /v1/policies/active                    200 without admin key
GET /v1/policies/active + matching ETag    304
GET /v1/admin/policies                     401 without key
POST /v1/admin/policies/validate           200 valid draft
POST /v1/admin/policies/validate           422 invalid guardrail
POST /v1/admin/policies/preview            200 and no repository write
POST /v1/admin/policies/publish            201 creates v2
POST /v1/admin/policies/publish stale      409
POST /v1/admin/policies/1/rollback         201 creates v3
GET /metrics                               200 Prometheus text
```

Patch `secrets.compare_digest` only through supplied keys; do not bypass the dependency itself.

- [ ] **Step 5: Implement file-based admin-key authentication**

`automation/app/policy_auth.py`:

```python
class PolicyAdminAuthenticator:
    def __init__(self, expected_key: str):
        if not expected_key:
            raise RuntimeError("policy administrator key must not be empty")
        self._expected_key = expected_key

    @classmethod
    def from_file(cls, key_file: Path) -> "PolicyAdminAuthenticator":
        return cls(key_file.read_text(encoding="utf-8").strip())

    @classmethod
    def from_environment_for_local_development(cls) -> "PolicyAdminAuthenticator":
        if os.getenv("POLICY_ALLOW_ENV_ADMIN_KEY") != "true":
            raise RuntimeError("environment policy key is disabled")
        return cls(os.environ["POLICY_ADMIN_KEY"])

    def require(self, authorization: str | None) -> None:
        supplied = authorization.removeprefix("Bearer ").strip() if authorization else ""
        if not secrets.compare_digest(supplied, self._expected_key):
            raise HTTPException(status_code=401, detail="invalid policy administrator credentials")
```

Do not include the supplied value in exceptions or logs.
Production `get_app()` must call `from_file`; the environment factory is only
used by explicit local-Compose wiring. Add tests proving an environment key is
rejected unless `POLICY_ALLOW_ENV_ADMIN_KEY=true`.

- [ ] **Step 6: Implement Policy API routes**

Use a dedicated `APIRouter` and exact endpoints:

```python
@router.get("/v1/policies/active")
@router.get("/v1/admin/policies")
@router.get("/v1/admin/policies/{version}")
@router.post("/v1/admin/policies/validate")
@router.post("/v1/admin/policies/preview")
@router.post("/v1/admin/policies/publish", status_code=201)
@router.post("/v1/admin/policies/{version}/rollback", status_code=201)
```

`GET /v1/policies/active` serializes an `ActivePolicyResponse`:

```python
ActivePolicyResponse(
    version=manager.active.version,
    schema_version=manager.active.policy.schema_version,
    published_at=manager.active.created_at,
    policy=manager.active.policy,
)
```

It must not expose `created_by`, `change_note`, `rollback_from`, or archived
history. Return `ETag: "policy-v{version}"` and honor `If-None-Match`.

Map:

```text
PolicyConflict / RepositoryConflict -> 409
Pydantic/guardrail validation       -> 422
RepositoryUnavailable               -> 503
missing history version             -> 404
bad admin key                       -> 401
```

The preview route calls Gateway through an injected `GatewaySimulator`:

```python
class GatewaySimulator(Protocol):
    def simulate(self, draft: PolicyDraft, cases: list[GatewayRequest]) -> list[dict]: ...
```

- [ ] **Step 7: Add Automation policy metrics**

`automation/app/policy_metrics.py` defines:

```python
ACTIVE_VERSION = Gauge(
    "polygate_policy_active_version",
    "Active policy version served by the Policy Control Plane.",
)
PUBLICATIONS = Counter(
    "polygate_policy_publications_total",
    "Policy publication attempts.",
    ["action", "result"],
)
LAST_PUBLISH = Gauge(
    "polygate_policy_last_publish_timestamp_seconds",
    "Unix timestamp of the last successful policy publication.",
)
```

Allowed labels:

```text
action=publish|rollback
result=success|rejected|degraded
```

Expose `GET /metrics` using `generate_latest()`.

- [ ] **Step 8: Wire production `get_app()`**

Kubernetes production `get_app()` must require:

```text
AUTOMATION_REDIS_URL
POLICY_ADMIN_KEY_FILE
POLICY_CONFIGMAP_NAME
POLICY_CONFIGMAP_KEY
POD_NAMESPACE
```

It builds:

```text
RedisAutomationStore
KubernetesConfigMapPolicyRepository
RedisPolicyCache key polygate:policy:active
PolicyManager
HttpGatewaySimulator using GATEWAY_URL
PolicyAdminAuthenticator.from_file
```

Importing `automation.app.main` without environment variables must remain safe.

- [ ] **Step 9: Run API/import regression suites**

```bash
python -m pytest automation/tests/test_kubernetes_policy_repository.py -v
python -m pytest automation/tests/test_policy_api.py -v
python -m pytest automation/tests/test_app_import_safety.py -v
python -m pytest automation/tests/ -v
```

Expected: all pass without setting production environment variables.

- [ ] **Step 10: Commit**

```bash
git add automation/app automation/tests automation/Dockerfile
git commit -m "feat(automation): add private policy control plane API"
```

---

### Task 4: Make Worker Queue Parameters Hot-Reloadable

**Owner:** B

**Files:**
- Create: `automation/app/policy_runtime.py`
- Create: `automation/tests/test_policy_runtime.py`
- Modify: `automation/app/redis_store.py`
- Modify: `automation/app/worker.py`
- Modify: `automation/app/models.py`
- Modify: `automation/tests/test_redis_store_and_worker.py`

**Interfaces:**
- Consumes: `GET /v1/policies/active` and Task 2 `PolicyDraft`.
- Produces: `PolicyRuntime.snapshot()` returning immutable `ActivePolicyResponse`.
- Produces: Worker loaded-version and reload-failure metrics.

- [ ] **Step 1: Write failing runtime-client tests**

Test:

```python
runtime = PolicyRuntime(
    mounted_file=policy_file,
    policy_url="http://automation:8020/v1/policies/active",
    refresh_seconds=5,
    transport=mock_transport,
)
assert runtime.snapshot().version == 1
runtime.refresh_once()
assert runtime.snapshot().version == 2
```

Cover:

- `If-None-Match` request;
- 304 keeps object identity;
- network error keeps v1;
- invalid v2 keeps v1 and records failure;
- mounted ConfigMap initializes after process restart by parsing
  `PolicyStoreDocument` and selecting its `active` version.

- [ ] **Step 2: Implement thread-safe PolicyRuntime**

Use a lock only during pointer replacement:

```python
class PolicyRuntime:
    def snapshot(self) -> ActivePolicyResponse:
        with self._lock:
            return self._active.model_copy(deep=True)

    def refresh_once(self) -> bool:
        response = self._client.get(
            self._policy_url,
            headers={"If-None-Match": f'"policy-v{self._active.version}"'},
        )
        if response.status_code == 304:
            return False
        response.raise_for_status()
        candidate = ActivePolicyResponse.model_validate(response.json())
        with self._lock:
            self._active = candidate
        return True
```

Implement a daemon refresh thread stopped during graceful shutdown.

- [ ] **Step 3: Write failing dynamic queue-policy tests**

Replace tests that depend on class constants with injected policy:

```python
store.claim_next_job(
    lease_seconds=60,
    queue_policy=QueuePolicy(
        waiting_bonus_interval_seconds=1,
        waiting_bonus_points=20,
        waiting_bonus_cap=100,
        starvation_streak_threshold=2,
        starvation_wait_seconds=3,
    ),
)
```

Assert:

- existing Job `initial_score` remains unchanged;
- a changed waiting bonus changes claim order;
- changed starvation threshold applies immediately;
- `policy_version` survives enqueue and serialization.

- [ ] **Step 4: Parameterize Redis queue selection**

Change:

```python
def claim_next_job(
    self,
    lease_seconds: int = 60,
    queue_policy: QueuePolicy | None = None,
) -> JobRecord | None:
```

Use `queue_policy` values instead of:

```text
_WAITING_BONUS_CAP
_WAITING_BONUS_PER_SECONDS
_STARVATION_STREAK_THRESHOLD
_STARVATION_WAIT_SECONDS
```

Keep safe v1 defaults when tests call without a policy.

- [ ] **Step 5: Pass policy snapshots through Worker**

At every claim:

```python
policy_version = policy_runtime.snapshot()
job = store.claim_next_job(
    lease_seconds=LEASE_SECONDS,
    queue_policy=policy_version.policy.automation.queue,
)
```

Do not recompute a queued Job's `initial_score`.

- [ ] **Step 6: Add Worker metrics**

Define:

```python
POLICY_LOADED_VERSION = Gauge(
    "polygate_policy_loaded_version",
    "Policy version loaded by this component.",
    ["component"],
)
POLICY_RELOAD_FAILURES = Counter(
    "polygate_policy_reload_failures_total",
    "Policy reload failures.",
    ["component", "reason"],
)
```

Use fixed label:

```text
component=automation-worker
reason=network|http|validation|file
```

- [ ] **Step 7: Run Worker tests**

```bash
python -m pytest automation/tests/test_policy_runtime.py -v
python -m pytest automation/tests/test_redis_store_and_worker.py -v
python -m pytest automation/tests/ -v
```

Expected: all pass with Redis test DB 15.

- [ ] **Step 8: Commit**

```bash
git add automation/app automation/tests
git commit -m "feat(automation): hot reload queue policy in worker"
```

---

### Task 5: Parameterize Gateway Routing and Add Last Known Good

**Owner:** A

**Files:**
- Create: `gateway/app/policy.py`
- Create: `gateway/tests/test_policy_runtime.py`
- Modify: `gateway/app/router.py`
- Modify: `gateway/app/main.py`
- Modify: `gateway/tests/test_router_quality.py`
- Modify: `gateway/README.md`

**Interfaces:**
- Consumes: Task 1 `policy.schema.json` gateway section and `GET /v1/policies/active`.
- Produces: `GatewayPolicyRuntime.snapshot() -> GatewayPolicySnapshot`.
- Produces: parameterized `select_provider(..., policy=...)`.

- [ ] **Step 1: Write failing Gateway policy-client tests**

Cover:

```text
mounted policy v1 loads on startup
HTTP v2 replaces v1
304 preserves v1
timeout preserves Last Known Good
invalid policy preserves Last Known Good
refresh interval defaults to 5 seconds
```

Use `httpx.MockTransport`; no live Automation dependency.

- [ ] **Step 2: Implement strict Gateway policy models and runtime**

In `gateway/app/policy.py` define:

```python
class GatewayRoutingPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid")
    assumed_output_tokens: int = Field(ge=1, le=32768)
    balanced_price_tolerance: float = Field(ge=0, le=2)
    budget_mode: Literal["soft", "hard"]
    latency_mode: Literal["soft", "hard"]
    high_quality_strategy: Literal["prefer_real", "lowest_cost"]


class GatewayPolicySnapshot(BaseModel):
    version: int = Field(ge=1)
    gateway: GatewayRoutingPolicy
```

`GatewayPolicyRuntime` parses the mounted `policy-store.json`, selects the
version referenced by `active_version`, and builds its initial snapshot from
that version's `policy.gateway`. It then polls `POLICY_API_URL`, honors
`POLICY_REFRESH_SECONDS=5`, and retains Last Known Good. The HTTP response uses
the Task 2 `ActivePolicyResponse` envelope, so the Gateway constructs later
snapshots from `body["version"]` and `body["policy"]["gateway"]`; it must not
expect a top-level `gateway` field.

- [ ] **Step 3: Write failing parameterized-router tests**

Add exact cases:

```python
policy = GatewayRoutingPolicy(
    assumed_output_tokens=512,
    balanced_price_tolerance=0.8,
    budget_mode="hard",
    latency_mode="hard",
    high_quality_strategy="lowest_cost",
)
```

Assert:

- cost estimation uses 512 output tokens;
- hard budget raises when none affordable;
- soft budget falls back to cheapest;
- hard latency raises when none meet target;
- soft latency relaxes;
- `prefer_real` and `lowest_cost` choose different Providers;
- privacy/capability guardrails remain unchanged.

- [ ] **Step 4: Parameterize `select_provider`**

Change signature:

```python
def select_provider(
    providers: list[dict],
    messages: list[dict],
    c,
    health: dict | None = None,
    required_capabilities: set[str] | None = None,
    policy: GatewayRoutingPolicy | None = None,
):
```

Use safe v1 defaults when `policy is None`. Implement:

```python
if not affordable and policy.budget_mode == "hard":
    raise RuntimeError(f"no provider satisfies budget ${c.max_cost_usd}")

if not within_latency and policy.latency_mode == "hard":
    raise RuntimeError(f"no provider satisfies latency {c.latency_target_ms}ms")
```

For `quality=high`, choose minimum-cost Provider when
`high_quality_strategy == "lowest_cost"`.

- [ ] **Step 5: Wire one immutable snapshot per request**

At the beginning of `chat_completions`:

```python
policy_snapshot = POLICY_RUNTIME.snapshot()
request.state.policy_version = policy_snapshot.version
```

Pass the same snapshot to initial routing and all failover routing within that request. A policy update during an in-flight request must not mix versions.

- [ ] **Step 6: Start and stop refresh task**

Use the existing Gateway startup/shutdown hooks. Start one policy refresh task alongside health checking and cancel it during shutdown.

- [ ] **Step 7: Run Gateway tests**

```bash
python -m pytest gateway/tests/test_policy_runtime.py -v
python -m pytest gateway/tests/test_router_quality.py -v
python -m pytest gateway/tests/ -v
```

Expected: all pass.

- [ ] **Step 8: Commit**

```bash
git add gateway/app gateway/tests gateway/README.md
git commit -m "feat(gateway): hot reload parameterized routing policy"
```

---

### Task 6: Add Gateway Simulation, Cache Isolation, Header, and Metrics

**Owner:** A

**Files:**
- Modify: `gateway/app/cache.py`
- Modify: `gateway/app/main.py`
- Modify: `gateway/app/metrics.py`
- Modify: `gateway/app/models.py`
- Create: `gateway/tests/test_policy_simulation.py`
- Create: `gateway/tests/test_policy_cache_isolation.py`
- Modify: `gateway/tests/test_metrics.py`
- Modify: `gateway/tests/test_cache_key_constraints.py`
- Modify: `contracts/README.md`

**Interfaces:**
- Consumes: Task 5 policy runtime and Task 1 cache contract.
- Produces: `POST /internal/routing/simulate`.
- Produces: `X-PolyGate-Policy-Version`.
- Produces: Gateway loaded-version/reload-failure metrics.

- [ ] **Step 1: Write failing cache-version tests**

Assert:

```python
v1 = cache_key(messages, "standard", "auto", "balanced", 0.01, 3000, policy_version=1)
v2 = cache_key(messages, "standard", "auto", "balanced", 0.01, 3000, policy_version=2)
assert v1 != v2
```

Also assert the same version and normalized request remain stable.

- [ ] **Step 2: Add policy version to cache key**

Change:

```python
def cache_key(
    messages: list[dict],
    privacy: str,
    scope: str,
    quality: str = "",
    max_cost_usd: float = 0.0,
    latency_target_ms: int = 0,
    policy_version: int = 1,
) -> str:
```

Append `str(policy_version)` to the canonical string before SHA-256.

- [ ] **Step 3: Write failing simulation tests**

Call `/internal/routing/simulate` with:

```json
{
  "request": {
    "model": "auto",
    "messages": [{"role": "user", "content": "policy simulation"}],
    "polygate": {
      "quality": "high",
      "privacy": "standard",
      "max_cost_usd": 0.01,
      "latency_target_ms": 3000
    }
  },
  "gateway_policy": {
    "assumed_output_tokens": 256,
    "balanced_price_tolerance": 0.2,
    "budget_mode": "soft",
    "latency_mode": "soft",
    "high_quality_strategy": "lowest_cost"
  }
}
```

Assert:

- response contains provider, reason, estimated cost, typical latency;
- adapter/provider call mocks have zero calls;
- cache get/set mocks have zero calls;
- privacy and capability guardrails apply;
- endpoint is absent from OpenAPI schema.

- [ ] **Step 4: Implement internal simulation**

Define strict request/response Pydantic models and:

```python
@app.post("/internal/routing/simulate", include_in_schema=False)
def simulate_routing(body: RoutingSimulationRequest) -> RoutingSimulationResponse:
```

Use current Provider registry and health snapshot, but use the draft
`gateway_policy` supplied by Automation. Never call adapters.

- [ ] **Step 5: Add policy-version response header**

For JSON and cache responses:

```http
X-PolyGate-Policy-Version: 5
```

For SSE add the same header to `StreamingResponse`. Add it to CORS
`expose_headers`. Do not change Decision Card schema.

- [ ] **Step 6: Add Gateway policy metrics**

In `gateway/app/metrics.py`:

```python
POLICY_LOADED_VERSION = Gauge(
    "polygate_policy_loaded_version",
    "Policy version loaded by this component.",
    ["component"],
)
POLICY_RELOAD_FAILURES = Counter(
    "polygate_policy_reload_failures_total",
    "Policy reload failures.",
    ["component", "reason"],
)
```

Use:

```text
component=gateway
reason=network|http|validation|file
```

- [ ] **Step 7: Run complete Gateway suite**

```bash
python -m pytest gateway/tests/test_policy_simulation.py -v
python -m pytest gateway/tests/test_policy_cache_isolation.py -v
python -m pytest gateway/tests/test_metrics.py -v
python -m pytest gateway/tests/ -v
```

Expected: all pass.

- [ ] **Step 8: Commit**

```bash
git add gateway/app gateway/tests contracts/README.md
git commit -m "feat(gateway): expose policy-aware routing simulation"
```

---

### Task 7: Build the Private Policy Editor

**Owner:** D

**Files:**
- Create: `automation/admin/index.html`
- Create: `automation/admin/policy-admin.js`
- Create: `automation/admin/policy-admin.css`
- Create: `automation/tests/test_policy_admin_ui.py`
- Modify: `automation/app/main.py`
- Modify: `automation/Dockerfile`
- Modify: `automation/README.md`

**Interfaces:**
- Consumes: Task 1 examples and Task 3 Admin API.
- Produces: `/admin/policies` static UI.
- Does not use: localStorage, iframe, public Web NodePort, Grafana credentials.

- [ ] **Step 1: Write failing UI-serving tests**

Using `TestClient`, assert:

```python
response = client.get("/admin/policies")
assert response.status_code == 200
assert "PolyGate Policy Management" in response.text
assert "POLICY_ADMIN_KEY" not in response.text
assert "localStorage" not in response.text
```

Also request JS/CSS assets and require 200.

- [ ] **Step 2: Mount static assets**

Mount:

```python
app.mount(
    "/admin/assets",
    StaticFiles(directory=ADMIN_ASSET_DIR),
    name="policy-admin-assets",
)
```

Serve `automation/admin/index.html` at `/admin/policies`.

- [ ] **Step 3: Implement key-in-memory session**

Use module state only:

```javascript
let adminKey = "";

function authHeaders() {
  return {
    "Content-Type": "application/json",
    Authorization: `Bearer ${adminKey}`,
  };
}
```

Do not write the key to URL, localStorage, sessionStorage, cookies, console, DOM attributes, or error messages.

- [ ] **Step 4: Implement editor sections**

Render:

```text
Active version/status
Gateway routing controls
Urgency score controls
Scenario weights/defaults
Queue policy controls
Locked guardrails
Change note
Validation messages
Impact preview
Version history
```

Finance privacy must be a disabled `high` control. Publish button remains disabled until the current draft has a successful validate and preview result.

- [ ] **Step 5: Implement API transitions**

Buttons call:

```text
Validate -> POST /v1/admin/policies/validate
Preview  -> POST /v1/admin/policies/preview
Publish  -> POST /v1/admin/policies/publish
Rollback -> POST /v1/admin/policies/{version}/rollback
```

After any edit, invalidate previous validation and preview state. On 409, show:

```text
The active policy changed while you were editing. Reload the latest version and preview again.
```

- [ ] **Step 6: Add UI regression tests**

Tests must verify:

- no secret embedded in generated HTML;
- guardrail labels exist;
- all five Gateway controls exist;
- all four urgency and scenario controls exist;
- change note input exists;
- Validate/Preview/Publish/History controls exist;
- `localStorage` and `sessionStorage` strings are absent.

- [ ] **Step 7: Build exact Automation image and smoke the page**

```bash
docker build \
  --platform linux/amd64 \
  --file automation/Dockerfile \
  --tag polygate-automation:policy-ui \
  .
```

Run the Automation UI/API test suite in the image with contracts mounted.

- [ ] **Step 8: Commit**

```bash
git add automation/admin automation/app/main.py automation/Dockerfile automation/tests automation/README.md
git commit -m "feat(automation): add private policy editor"
```

---

### Task 8: Add Kubernetes Policy Bootstrap, Secret, and Least-Privilege RBAC

**Owner:** C

**Files:**
- Create: `deploy/policy-rbac.yaml`
- Create: `scripts/render-default-policy-store.py`
- Create: `scripts/tests/test-deployment-policy.sh`
- Modify: `deploy/automation.yaml`
- Modify: `deploy/gateway.yaml`
- Modify: `scripts/deploy-kubernetes-application.sh`
- Modify: `scripts/kubernetes-monitoring-preflight.sh`
- Modify: `scripts/tests/test-deployment-automation.sh`
- Modify: `docker-compose.yml`
- Modify: `deploy/README.md`
- Modify: `deploy/RUNBOOK.md`

**Interfaces:**
- Consumes: `contracts/policy-examples.json` store example exported as `policy-store.json`.
- Produces: `polygate-routing-policy`, `polygate-policy-admin`, ServiceAccount/Role/RoleBinding.
- Produces: exact runtime environment variables and volume paths.

- [ ] **Step 1: Write failing deployment-policy checks**

`scripts/tests/test-deployment-policy.sh` must assert:

```text
ServiceAccount/polygate-policy-controller exists
Role resourceNames contains polygate-routing-policy only
Role verbs are get and update
Role does not grant Secret, Pod, Deployment, list, watch, create, patch, delete
Automation uses the ServiceAccount
Worker still uses automountServiceAccountToken: false
Gateway/Automation/Worker mount /config/policy-store.json read-only
Automation mounts admin-key Secret read-only
Gateway and Worker set POLICY_API_URL=http://automation:8020
refresh interval is 5
no policy Service or NodePort exists
deploy script only creates ConfigMap when absent
deploy script refuses to continue when admin Secret is absent
```

- [ ] **Step 2: Run and verify failure**

```bash
bash scripts/tests/test-deployment-policy.sh
```

Expected: missing `deploy/policy-rbac.yaml`.

- [ ] **Step 3: Add least-privilege RBAC**

`deploy/policy-rbac.yaml` contains:

```yaml
apiVersion: v1
kind: ServiceAccount
metadata:
  name: polygate-policy-controller
---
apiVersion: rbac.authorization.k8s.io/v1
kind: Role
metadata:
  name: polygate-policy-controller
rules:
  - apiGroups: [""]
    resources: ["configmaps"]
    resourceNames: ["polygate-routing-policy"]
    verbs: ["get", "update"]
---
apiVersion: rbac.authorization.k8s.io/v1
kind: RoleBinding
metadata:
  name: polygate-policy-controller
subjects:
  - kind: ServiceAccount
    name: polygate-policy-controller
roleRef:
  apiGroup: rbac.authorization.k8s.io
  kind: Role
  name: polygate-policy-controller
```

- [ ] **Step 4: Add bootstrap-without-overwrite deployment logic**

Before application manifests:

```bash
POLICY_STORE_TMP="$(mktemp "${TMPDIR:-/tmp}/polygate-policy-store.XXXXXX")"
trap 'rm -f "$POLICY_STORE_TMP"' EXIT

if ! kubectl get configmap polygate-routing-policy \
  --namespace "$NAMESPACE" >/dev/null 2>&1; then
  python3 "$ROOT_DIR/scripts/render-default-policy-store.py" \
    "$ROOT_DIR/contracts/policy-examples.json" \
    > "$POLICY_STORE_TMP"
  kubectl create configmap polygate-routing-policy \
    --namespace "$NAMESPACE" \
    --from-file="policy-store.json=$POLICY_STORE_TMP"
else
  echo "Preserving existing polygate-routing-policy ConfigMap and version history"
fi
```

Use `mktemp` and a trap for cleanup. Never run `kubectl apply` with default policy over an existing ConfigMap.

Require:

```bash
kubectl get secret polygate-policy-admin --namespace "$NAMESPACE"
```

before applying Automation.

- [ ] **Step 5: Mount policy and admin key**

Exact paths:

```text
/config/policy-store.json
/var/run/secrets/polygate-policy/admin-key
```

Automation environment:

```text
POLICY_FILE=/config/policy-store.json
POLICY_ADMIN_KEY_FILE=/var/run/secrets/polygate-policy/admin-key
POLICY_CONFIGMAP_NAME=polygate-routing-policy
POLICY_CONFIGMAP_KEY=policy-store.json
POLICY_REFRESH_SECONDS=5
POD_NAMESPACE from metadata.namespace
```

Gateway/Worker:

```text
POLICY_FILE=/config/policy-store.json
POLICY_API_URL=http://automation:8020
POLICY_REFRESH_SECONDS=5
```

Only Automation uses `serviceAccountName: polygate-policy-controller`.
Mount the ConfigMap as a directory-backed projected volume and read
`/config/policy-store.json`; do not use `subPath`, because Kubernetes does not
propagate ConfigMap updates into an existing `subPath` mount. Mount the Secret
as its own read-only directory.

- [ ] **Step 6: Update local Compose**

Mount the rendered default store into Gateway, Automation, and Worker. Set:

```text
POLICY_ADMIN_KEY=local-policy-admin-development
POLICY_ALLOW_ENV_ADMIN_KEY=true
POLICY_API_URL=http://automation:8020
POLICY_REFRESH_SECONDS=5
```

For Compose only, allow Automation to use `InMemoryPolicyRepository` initialized
from the mounted file and the explicitly enabled development environment key.
Kubernetes must neither set `POLICY_ALLOW_ENV_ADMIN_KEY` nor inject a plaintext
`POLICY_ADMIN_KEY` environment variable. Do not require Kubernetes API locally.

- [ ] **Step 7: Extend preflight**

Add `deploy/policy-rbac.yaml` to kubeconform checks and run the deployment-policy regression script from preflight.

- [ ] **Step 8: Run local C-line checks**

```bash
bash scripts/tests/test-deployment-policy.sh
bash scripts/tests/test-deployment-automation.sh
./scripts/kubernetes-monitoring-preflight.sh
```

Expected: all pass.

- [ ] **Step 9: Commit**

```bash
git add \
  deploy/policy-rbac.yaml \
  deploy/automation.yaml \
  deploy/gateway.yaml \
  deploy/README.md \
  deploy/RUNBOOK.md \
  docker-compose.yml \
  scripts/deploy-kubernetes-application.sh \
  scripts/kubernetes-monitoring-preflight.sh \
  scripts/tests/test-deployment-policy.sh \
  scripts/tests/test-deployment-automation.sh \
  scripts/render-default-policy-store.py
git commit -m "feat(deploy): add persistent policy control plane wiring"
```

---

### Task 9: Add Policy Observability and Grafana Management Entry

**Owner:** C

**Files:**
- Modify: `monitoring/prometheus/prometheus-kubernetes.yml`
- Modify: `monitoring/grafana/dashboards/polygate-overview.json`
- Modify: `scripts/kubernetes-monitoring-preflight.sh`
- Modify: `scripts/kubernetes-monitoring-smoke-test.sh`
- Modify: `deploy/monitoring/README.md`
- Create: `scripts/kubernetes-policy-smoke-test.sh`

**Interfaces:**
- Consumes: metric names from Task 1 and endpoints from Tasks 3/4/6.
- Produces: Policy API Prometheus target and Policy Management dashboard row.

- [ ] **Step 1: Write failing monitoring assertions**

Extend preflight required metrics with:

```text
polygate_policy_active_version
polygate_policy_loaded_version
polygate_policy_publications_total
polygate_policy_reload_failures_total
polygate_policy_last_publish_timestamp_seconds
```

Require unique dashboard panel titles:

```text
Active Policy Version
Gateway Loaded Policy
Worker Loaded Policy
Policy Publication Outcomes
Policy Reload Failures
Last Policy Publication
Open Policy Editor
```

- [ ] **Step 2: Add Automation API scrape job**

Add `polygate-automation-api` Pod discovery:

```yaml
- job_name: polygate-automation-api
  metrics_path: /metrics
  kubernetes_sd_configs:
    - role: pod
      namespaces:
        names: [default]
  relabel_configs:
    - source_labels: [__meta_kubernetes_pod_label_app]
      regex: automation
      action: keep
    - source_labels: [__meta_kubernetes_pod_container_name]
      regex: automation
      action: keep
    - source_labels: [__meta_kubernetes_pod_container_port_number]
      regex: "8020"
      action: keep
```

- [ ] **Step 3: Add Grafana policy panels**

Queries:

```promql
max(polygate_policy_active_version)
min(polygate_policy_loaded_version{component="gateway"})
max(polygate_policy_loaded_version{component="automation-worker"})
sum by (action, result) (polygate_policy_publications_total)
sum by (component, reason) (rate(polygate_policy_reload_failures_total[$__rate_interval]))
max(polygate_policy_last_publish_timestamp_seconds)
```

Add a drift panel:

```promql
max(polygate_policy_active_version)
-
min(polygate_policy_loaded_version)
```

Value 0 is green; non-zero is red after 30 seconds.

The Text panel link is:

```text
http://localhost:8020/admin/policies
```

- [ ] **Step 4: Extend monitoring smoke**

With `INCLUDE_POLICY=1`, require:

```promql
max(up{job="polygate-automation-api"}) == 1
count(polygate_policy_active_version) > 0
count(polygate_policy_loaded_version{component="gateway"}) == gateway target count
count(polygate_policy_loaded_version{component="automation-worker"}) == 1
```

Also inspect Grafana API response for all required metric expressions and panel titles.

- [ ] **Step 5: Create policy lifecycle smoke test**

`scripts/kubernetes-policy-smoke-test.sh` requires:

```text
AUTOMATION_URL=http://localhost:8020
PROMETHEUS_URL=http://localhost:9090
POLICY_ADMIN_KEY environment variable
```

It must:

1. read active version;
2. verify an invalid key returns 401;
3. validate a changed `high_quality_strategy`;
4. preview and assert no Provider call side effects;
5. publish next version;
6. poll Prometheus until two Gateway targets and Worker load it;
7. rollback to original content, creating another version;
8. poll until all components converge again;
9. never print the admin key.

Use a trap to attempt rollback if the script exits after publishing.

- [ ] **Step 6: Run Prometheus/Grafana validation**

```bash
./scripts/kubernetes-monitoring-preflight.sh
```

Expected:

```text
Prometheus config valid
all Grafana PromQL rules valid
all policy panels present
all Kubernetes schemas valid
```

- [ ] **Step 7: Commit**

```bash
git add \
  monitoring/prometheus/prometheus-kubernetes.yml \
  monitoring/grafana/dashboards/polygate-overview.json \
  scripts/kubernetes-monitoring-preflight.sh \
  scripts/kubernetes-monitoring-smoke-test.sh \
  scripts/kubernetes-policy-smoke-test.sh \
  deploy/monitoring/README.md
git commit -m "feat(monitoring): visualize policy rollout and drift"
```

---

### Task 10: Local Cross-Lane Integration Gate

**Owner:** C coordinates; A/B/D resolve their failures.

**Files:**
- Modify: `scripts/automation-peak-test.sh`
- Modify: `scripts/kubernetes-automation-smoke-test.sh`
- Modify: `README.md`
- Modify: `deploy/README.md`
- Modify: `automation/README.md`

**Interfaces:**
- Consumes: all prior merged tasks.
- Produces: one reproducible local acceptance sequence and corrected peak demo.

- [ ] **Step 1: Repair the peak-test payload**

Replace unsupported `{"raw_text": ...}` with four complete intents:

```text
critical production_incident
high customer_escalation
normal finance_summary
low marketing_batch
```

Every intent includes employee, department, scenario, urgency, prompt, and complete preferences. Print submitted priority score, policy version, claim/start order, final status, and queue wait.

- [ ] **Step 2: Add policy fields to Automation smoke assertions**

Assert Preview and Job contain integer `policy_version`, and the completed Job retains the same version as submission even if an unrelated active-policy read occurs during polling.

- [ ] **Step 3: Start the full local stack**

```bash
docker compose up --build -d
docker compose ps
```

Expected healthy:

```text
redis
mock-a
mock-b
gateway
automation
automation-worker
web
prometheus
grafana
```

- [ ] **Step 4: Run all backend tests in Python 3.12 containers**

Run exact Gateway and Automation test suites inside their built images. Mount `contracts/` read-only for alignment tests and use Redis DB 15 for Worker tests.

Expected:

```text
gateway/tests: all pass
automation/tests: all pass
```

- [ ] **Step 5: Run Web tests**

```bash
cd web
npm test
npm run lint
npm run build
cd ..
```

Expected: all commands exit 0.

- [ ] **Step 6: Run contract and deployment tests**

```bash
python3 scripts/tests/test-automation-contracts.py
python3 scripts/tests/test-policy-contracts.py
bash scripts/tests/test-deployment-automation.sh
bash scripts/tests/test-deployment-policy.sh
./scripts/kubernetes-monitoring-preflight.sh
```

Expected: all exit 0.

- [ ] **Step 7: Run local behavior smoke tests**

```bash
./scripts/web-smoke-test.sh
./scripts/kubernetes-automation-smoke-test.sh
POLICY_ADMIN_KEY=local-policy-admin-development \
  ./scripts/kubernetes-policy-smoke-test.sh
./scripts/automation-peak-test.sh
```

For local policy smoke, override URLs to local Compose services.

- [ ] **Step 8: Verify security invariants**

Confirm:

```text
Policy Editor is not proxied by Web Nginx
Admin key is absent from docker logs
privacy=high cannot route to real-a
finance privacy cannot be changed
/internal/routing/simulate is absent from OpenAPI and Web proxy
existing ConfigMap is not overwritten by deployment dry run
```

- [ ] **Step 9: Commit integration docs and smoke fixes**

```bash
git add \
  scripts/automation-peak-test.sh \
  scripts/kubernetes-automation-smoke-test.sh \
  README.md \
  deploy/README.md \
  automation/README.md
git commit -m "test: add policy management integration gate"
```

---

### Task 11: Deploy and Verify on EKS

**Owner:** C runs all AWS/EKS commands.

**Files:**
- Evidence only; do not commit secrets.
- Update `deploy/RUNBOOK.md` only if runtime evidence reveals a missing operational step.

**Interfaces:**
- Consumes: merged and locally verified main.
- Produces: EKS evidence for hot update, rollback, persistence, drift, and user-facing continuity.

- [ ] **Step 1: Refresh Learner Lab credentials and verify account**

```bash
AWS_PAGER="" aws sts get-caller-identity
AWS_PAGER="" aws eks describe-cluster \
  --region us-east-1 \
  --name G3EKS \
  --query 'cluster.{name:name,status:status,version:version}' \
  --output table
aws eks --region us-east-1 update-kubeconfig --name G3EKS
kubectl get nodes
```

Expected account `356029564744`, cluster `G3EKS`, status `ACTIVE`, two Ready nodes.

- [ ] **Step 2: Create the admin Secret without printing it**

```bash
read -rsp "Policy admin key: " POLICY_ADMIN_KEY
printf "\n"
kubectl create secret generic polygate-policy-admin \
  --namespace default \
  --from-literal=admin-key="$POLICY_ADMIN_KEY" \
  --dry-run=client \
  --output=yaml \
  | kubectl apply --filename=-
unset POLICY_ADMIN_KEY
```

Verify keys without values:

```bash
kubectl get secret polygate-policy-admin \
  -o go-template='{{range $k,$v := .data}}{{printf "%s\n" $k}}{{end}}'
```

Expected: `admin-key`.

- [ ] **Step 3: Build and push immutable linux/amd64 images**

```bash
aws ecr get-login-password --region us-east-1 \
  | docker login \
      --username AWS \
      --password-stdin \
      356029564744.dkr.ecr.us-east-1.amazonaws.com

export IMAGE_TAG="$(git rev-parse --short=12 HEAD)"

ECR_REGISTRY=356029564744.dkr.ecr.us-east-1.amazonaws.com \
TARGET_PLATFORM=linux/amd64 \
IMAGE_TAG="$IMAGE_TAG" \
INCLUDE_AUTOMATION=1 \
PUSH_IMAGES=1 \
./scripts/build-kubernetes-images.sh
```

Expected: Gateway, Mock, Web, Automation all pushed under the same tag.

- [ ] **Step 4: Deploy without overwriting existing policy history**

```bash
IMAGE_TAG="$IMAGE_TAG" \
ECR_REGISTRY=356029564744.dkr.ecr.us-east-1.amazonaws.com \
INCLUDE_AUTOMATION=1 \
./scripts/deploy-kubernetes-application.sh
```

Expected output explicitly says either:

```text
Created initial polygate-routing-policy ConfigMap
```

or:

```text
Preserving existing polygate-routing-policy ConfigMap and version history
```

- [ ] **Step 5: Verify RBAC**

```bash
kubectl auth can-i get configmap/polygate-routing-policy \
  --as=system:serviceaccount:default:polygate-policy-controller
kubectl auth can-i update configmap/polygate-routing-policy \
  --as=system:serviceaccount:default:polygate-policy-controller
kubectl auth can-i list configmaps \
  --as=system:serviceaccount:default:polygate-policy-controller
kubectl auth can-i get secrets \
  --as=system:serviceaccount:default:polygate-policy-controller
```

Expected:

```text
yes
yes
no
no
```

- [ ] **Step 6: Deploy monitoring and establish private access**

```bash
./scripts/deploy-kubernetes-monitoring.sh
```

Keep four port-forwards:

```bash
kubectl port-forward service/automation 8020:8020
kubectl port-forward deployment/automation-worker 9000:9000
kubectl port-forward service/prometheus 9090:9090
kubectl port-forward service/grafana 3000:3000
```

- [ ] **Step 7: Run application, policy, and monitoring smoke**

Load the admin key into an environment variable without echoing it, then:

```bash
export POLICY_ADMIN_KEY="$(
  kubectl get secret polygate-policy-admin \
    -o jsonpath='{.data.admin-key}' \
  | python3 -c 'import base64,sys; print(base64.b64decode(sys.stdin.read()).decode())'
)"
export GRAFANA_PASSWORD="$(
  kubectl get secret polygate-grafana-admin \
    -o jsonpath='{.data.admin-password}' \
  | python3 -c 'import base64,sys; print(base64.b64decode(sys.stdin.read()).decode())'
)"

./scripts/kubernetes-automation-smoke-test.sh
./scripts/kubernetes-policy-smoke-test.sh
INCLUDE_AUTOMATION=1 INCLUDE_POLICY=1 \
  ./scripts/kubernetes-monitoring-smoke-test.sh
unset POLICY_ADMIN_KEY GRAFANA_PASSWORD
```

Expected: zero failures.

- [ ] **Step 8: Verify policy persistence**

Record active version, restart Automation, and read it again:

```bash
kubectl rollout restart deployment/automation
kubectl rollout status deployment/automation --timeout=180s
```

Expected active version and history unchanged.

Then restart Redis:

```bash
kubectl rollout restart deployment/redis
kubectl rollout status deployment/redis --timeout=180s
```

Expected Automation readiness recovers and active policy remains unchanged.

- [ ] **Step 9: Verify Last Known Good**

Temporarily scale Automation to zero only after recording the current version:

```bash
kubectl scale deployment/automation --replicas=0
```

Send a Web request and confirm Gateway still routes with the last loaded version. Restore immediately:

```bash
kubectl scale deployment/automation --replicas=1
kubectl rollout status deployment/automation --timeout=180s
```

Expected no Gateway outage.

- [ ] **Step 10: Capture evidence**

Capture:

```text
Policy Editor active version and impact preview
Publish success v1 -> v2
Grafana active/loaded versions converged
Provider selection before and after
Rollback v2 -> v3
ConfigMap history after Automation/Redis restart
RBAC yes/yes/no/no
All smoke summaries
```

- [ ] **Step 11: Final Git decision**

If EKS validation required no code/doc change, do not commit deployment state.

If Runbook changed, stage only:

```bash
git add deploy/RUNBOOK.md
git commit -m "docs(deploy): record policy management recovery procedure"
git push
```

Never commit Secret values, AWS credentials, node IPs, or generated screenshots containing keys.

---

## Pull Request Strategy

Use these PRs after the contract PR merges:

```text
PR A: feat(gateway): add runtime policy management
PR B: feat(automation): add policy control plane and worker reload
PR C: feat(deploy): wire policy persistence and observability
PR D: feat(automation-ui): add private policy editor
```

Each implementation PR must:

- rebase or merge latest `main` after Task 1;
- include its own tests;
- state consumed policy contract version;
- avoid editing another member's owned files unless listed in this plan;
- include exact validation output;
- mention any change to frozen contracts before merge.

Merge order after contracts:

```text
B lifecycle core
A Gateway runtime
B Policy API/Worker
D Editor
C Kubernetes/Monitoring
Integration fixes
```

## Definition of Done

The feature is complete only when:

- all local contract, Gateway, Automation, Web, deployment, PromQL, and schema tests pass;
- Policy Editor remains private and requires the admin key;
- publish and rollback generate monotonic versions;
- two Gateway Pods and Worker converge within 5 seconds under normal conditions;
- Last Known Good survives Policy API unavailability;
- active policy and 20-version history survive Automation and Redis restarts;
- cache cannot cross policy versions;
- immutable privacy/capability guardrails pass regression tests;
- Grafana shows active version, loaded versions, outcomes, failures, and editor link;
- Web Chat and OpenAI-compatible requests continue working;
- EKS smoke and recovery checks report zero failures.
