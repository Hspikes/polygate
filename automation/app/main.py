from __future__ import annotations

import json
import os
import shlex
import uuid
import redis
from pathlib import Path

from fastapi import APIRouter, Depends, FastAPI, Header, HTTPException, Query, Request, Response, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from pydantic import BaseModel, ConfigDict, Field

from automation.app.models import (
    AutomationIntent,
    GatewayMessage,
    GatewayRequest,
    GatewaySimulationRequest,
    JobRecord,
    JobSubmission,
    JobState,
    PreviewResponse,
    PriorityDecision,
    Snippets,
    TemplateDefinition,
)
from automation.app.kubernetes_policy_repository import KubernetesConfigMapPolicyRepository
from automation.app.policy_auth import PolicyAdminAuthenticator
from automation.app.policy_manager import (
    disambiguate_case_id,
    GatewaySimulationUnavailable,
    GatewaySimulator,
    HttpGatewaySimulator,
    PolicyManager,
    RedisPolicyCache,
)
from automation.app.policy_metrics import ACTIVE_VERSION, LAST_PUBLISH, PUBLICATIONS
from automation.app.policy_models import ActivePolicyResponse, PolicyDraft, PolicyStoreDocument, PolicyVersion
from automation.app.policy_repository import (
    InMemoryPolicyRepository,
    PolicyConflict,
    PolicyVersionNotFound,
    RepositoryUnavailable,
)
from automation.app.store import AutomationStore, InMemoryAutomationStore
from automation.app.templates import TEMPLATES
from automation.app.redis_store import RedisAutomationStore

PREVIEW_TTL_SECONDS = 600
POLYGATE_URL_DEFAULT = "http://localhost:8000"
POLYGATE_URL_PLACEHOLDER = "${POLYGATE_URL:-" + POLYGATE_URL_DEFAULT + "}"
ADMIN_DIR = Path(__file__).resolve().parents[1] / "admin"
ADMIN_ASSET_DIR = ADMIN_DIR
ADMIN_CONTENT_SECURITY_POLICY = (
    "default-src 'self'; "
    "script-src 'self'; "
    "style-src 'self'; "
    "img-src 'self' data:; "
    "connect-src 'self'; "
    "object-src 'none'; "
    "base-uri 'none'; "
    "frame-ancestors 'none'; "
    "form-action 'none'"
)


class PolicyPreviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    policy: PolicyDraft
    gateway_cases: list[GatewaySimulationRequest] = Field(default_factory=list)
    priority_cases: list[AutomationIntent] = Field(default_factory=list)


class PolicyPublishRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    base_version: int = Field(ge=1)
    change_note: str = Field(min_length=1, max_length=500)
    policy: PolicyDraft


class PolicyRollbackRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    base_version: int = Field(ge=1)
    change_note: str = Field(min_length=1, max_length=500)


def _policy_diff(before: object, after: object, path: str = "") -> list[dict[str, object]]:
    if isinstance(before, dict) and isinstance(after, dict):
        changes: list[dict[str, object]] = []
        for key in sorted(set(before) | set(after)):
            child_path = f"{path}.{key}" if path else key
            changes.extend(_policy_diff(before.get(key), after.get(key), child_path))
        return changes
    if before != after:
        return [{"path": path, "before": before, "after": after}]
    return []


def _publish_response(result) -> dict[str, object]:
    return {
        "version": result.version,
        "previous_version": result.previous_version,
        "rollback_from": result.rollback_from,
        "published_at": result.published_at,
        "warnings": result.warnings,
    }


def _policy_router(
    manager: PolicyManager,
    authenticator: PolicyAdminAuthenticator,
    gateway_simulator: GatewaySimulator,
) -> APIRouter:
    router = APIRouter()
    startup_active = manager.active
    ACTIVE_VERSION.set(startup_active.version)
    LAST_PUBLISH.set(startup_active.created_at.timestamp())

    def require_admin(authorization: str | None = Header(default=None)) -> None:
        authenticator.require(authorization)

    @router.get("/v1/policies/active", response_model=ActivePolicyResponse)
    def active_policy(if_none_match: str | None = Header(default=None, alias="If-None-Match")):
        active = manager.active
        etag = f'"policy-v{active.version}"'
        if if_none_match == etag:
            return Response(status_code=304, headers={"ETag": etag})
        return Response(
            content=ActivePolicyResponse(
                version=active.version,
                schema_version=active.policy.schema_version,
                published_at=active.created_at,
                policy=active.policy,
            ).model_dump_json(),
            media_type="application/json",
            headers={"ETag": etag},
        )

    @router.get("/v1/admin/policies", response_model=list[PolicyVersion])
    def list_policies(_: None = Depends(require_admin)) -> list[PolicyVersion]:
        return manager.history

    @router.get("/v1/admin/policies/{version}", response_model=PolicyVersion)
    def get_policy(version: int, _: None = Depends(require_admin)) -> PolicyVersion:
        record = manager.get_version(version)
        if record is None:
            raise HTTPException(status_code=404, detail="policy version not found")
        return record

    @router.post("/v1/admin/policies/validate")
    def validate_policy(draft: PolicyDraft, _: None = Depends(require_admin)) -> dict[str, object]:
        return {"valid": True, "warnings": manager.validate(draft)}

    @router.post("/v1/admin/policies/preview")
    def preview_policy(request: PolicyPreviewRequest, _: None = Depends(require_admin)) -> dict[str, object]:
        active = manager.active
        priority = manager.preview_priority(
            request.policy,
            request.priority_cases,
            active_policy=active.policy,
        )
        before_routing = gateway_simulator.simulate(active.policy, request.gateway_cases)
        after_routing = gateway_simulator.simulate(request.policy, request.gateway_cases)
        routing_slug_counts: dict[str, int] = {}
        return {
            "base_version": active.version,
            "diff": _policy_diff(
                active.policy.model_dump(mode="json"), request.policy.model_dump(mode="json")
            ),
            "warnings": manager.validate(request.policy),
            "simulations": {
                "routing": [
                    {
                        "case_id": disambiguate_case_id(
                            f"{case.polygate.quality}-{case.polygate.privacy}",
                            routing_slug_counts,
                        ),
                        "before": before,
                        "after": after,
                    }
                    for case, before, after in zip(request.gateway_cases, before_routing, after_routing)
                ],
                "priority": [simulation.__dict__ for simulation in priority],
                "queue": {
                    "before_order": [
                        simulation.case_id
                        for simulation in sorted(priority, key=lambda item: item.before_score, reverse=True)
                    ],
                    "after_order": [
                        simulation.case_id
                        for simulation in sorted(priority, key=lambda item: item.after_score, reverse=True)
                    ],
                },
            },
        }

    def record_publication(action: str, result) -> dict[str, object]:
        ACTIVE_VERSION.set(result.version)
        LAST_PUBLISH.set(result.published_at.timestamp())
        PUBLICATIONS.labels(action=action, result="degraded" if result.warnings else "success").inc()
        return _publish_response(result)

    @router.post("/v1/admin/policies/publish", status_code=201)
    def publish_policy(request: PolicyPublishRequest, _: None = Depends(require_admin)) -> dict[str, object]:
        try:
            result = manager.publish(
                base_version=request.base_version,
                draft=request.policy,
                change_note=request.change_note,
                actor="policy-admin",
            )
        except PolicyConflict as exc:
            PUBLICATIONS.labels(action="publish", result="rejected").inc()
            raise HTTPException(status_code=409, detail="policy version conflict") from exc
        except RepositoryUnavailable as exc:
            PUBLICATIONS.labels(action="publish", result="degraded").inc()
            raise HTTPException(status_code=503, detail="policy repository unavailable") from exc
        return record_publication("publish", result)

    @router.post("/v1/admin/policies/{version}/rollback", status_code=201)
    def rollback_policy(
        version: int,
        request: PolicyRollbackRequest,
        _: None = Depends(require_admin),
    ) -> dict[str, object]:
        try:
            result = manager.rollback(
                target_version=version,
                base_version=request.base_version,
                change_note=request.change_note,
                actor="policy-admin",
            )
        except PolicyVersionNotFound as exc:
            PUBLICATIONS.labels(action="rollback", result="rejected").inc()
            raise HTTPException(status_code=404, detail="policy version not found") from exc
        except PolicyConflict as exc:
            PUBLICATIONS.labels(action="rollback", result="rejected").inc()
            raise HTTPException(status_code=409, detail="policy version conflict") from exc
        except RepositoryUnavailable as exc:
            PUBLICATIONS.labels(action="rollback", result="degraded").inc()
            raise HTTPException(status_code=503, detail="policy repository unavailable") from exc
        return record_publication("rollback", result)

    return router


def _compile_preview(
    intent: AutomationIntent,
    policy_version: PolicyVersion,
) -> PreviewResponse:
    normalized = intent.model_copy(deep=True)
    adjustments: list[str] = []

    if normalized.scenario.value == "finance_summary" and normalized.preferences.privacy != "high":
        normalized.preferences.privacy = "high"
        adjustments.append("finance_summary requires privacy=high")

    automation_policy = policy_version.policy.automation
    urgency_score = automation_policy.urgency_scores.model_dump()[
        normalized.urgency.value
    ]
    scenario_policy = automation_policy.scenarios.model_dump()[
        normalized.scenario.value
    ]
    scenario_weight = scenario_policy["weight"]
    score = urgency_score + scenario_weight
    priority = PriorityDecision(
        **{
            "class": normalized.urgency,
            "initial_score": score,
            "reason": (
                f"{normalized.urgency.value} urgency ({urgency_score}) + "
                f"{normalized.scenario.value} scenario ({scenario_weight})"
            ),
        }
    )
    gateway_request = GatewayRequest(
        model="auto",
        messages=[GatewayMessage(role="user", content=normalized.prompt)],
        polygate=normalized.preferences,
    )
    payload = gateway_request.model_dump(mode="json")
    pretty_json = json.dumps(payload, ensure_ascii=False, indent=2)
    compact_json = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    snippets = Snippets(
        json=pretty_json,
        curl=(
            f"curl -X POST {POLYGATE_URL_PLACEHOLDER}/v1/chat/completions "
            f"-H 'Content-Type: application/json' -d {shlex.quote(compact_json)}"
        ),
        python=(
            "import os\n\n"
            "import requests\n\n"
            f'POLYGATE_URL = os.getenv("POLYGATE_URL", "{POLYGATE_URL_DEFAULT}")\n\n'
            f"payload = {pretty_json}\n"
            "response = requests.post(\n"
            "    f\"{POLYGATE_URL}/v1/chat/completions\", json=payload, timeout=30\n"
            ")\n"
            "response.raise_for_status()\n"
            "print(response.json())"
        ),
    )
    return PreviewResponse(
        preview_id="preview_" + uuid.uuid4().hex,
        expires_in_seconds=PREVIEW_TTL_SECONDS,
        normalized_intent=normalized,
        priority=priority,
        gateway_request=gateway_request,
        snippets=snippets,
        policy_adjustments=adjustments,
        policy_version=policy_version.version,
    )


def create_app(
    store: AutomationStore | None = None,
    policy_manager: PolicyManager | None = None,
    gateway_simulator: GatewaySimulator | None = None,
    policy_authenticator: PolicyAdminAuthenticator | None = None,
) -> FastAPI:
    app = FastAPI(title="PolyGate Automation API", version="0.1.0-skeleton")
    persistence = store or InMemoryAutomationStore()

    @app.middleware("http")
    async def secure_policy_admin(request: Request, call_next):
        response = await call_next(request)
        if request.url.path.startswith("/admin/"):
            response.headers["Content-Security-Policy"] = ADMIN_CONTENT_SECURITY_POLICY
            response.headers["Cache-Control"] = "no-store"
            response.headers["X-Content-Type-Options"] = "nosniff"
            response.headers["Referrer-Policy"] = "no-referrer"
        return response

    app.mount(
        "/admin/assets",
        StaticFiles(directory=ADMIN_ASSET_DIR, check_dir=True),
        name="policy-admin-assets",
    )

    @app.get("/admin/policies", include_in_schema=False)
    def policy_admin() -> FileResponse:
        return FileResponse(ADMIN_DIR / "index.html", media_type="text/html")

    @app.exception_handler(RepositoryUnavailable)
    def repository_unavailable(_, __):
        return JSONResponse(status_code=503, content={"detail": "policy repository unavailable"})

    @app.exception_handler(PolicyConflict)
    def policy_conflict(_, __):
        return JSONResponse(status_code=409, content={"detail": "policy version conflict"})

    @app.exception_handler(GatewaySimulationUnavailable)
    def gateway_simulation_unavailable(_, __):
        return JSONResponse(
            status_code=503,
            content={"detail": "gateway simulation unavailable"},
        )

    @app.exception_handler(RequestValidationError)
    def request_validation_error(request: Request, exc: RequestValidationError):
        if request.url.path == "/v1/admin/policies/publish":
            PUBLICATIONS.labels(action="publish", result="rejected").inc()
        elif request.url.path.startswith("/v1/admin/policies/") and request.url.path.endswith("/rollback"):
            PUBLICATIONS.labels(action="rollback", result="rejected").inc()
        details = [
            {key: error[key] for key in ("loc", "msg", "type") if key in error}
            for error in exc.errors()
        ]
        return JSONResponse(status_code=422, content={"detail": details})

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok", "service": "automation"}

    @app.get("/ready")
    def ready() -> dict[str, str]:
        if policy_manager is not None and not policy_manager.ready:
            raise HTTPException(
                status_code=503,
                detail="policy manager unavailable",
            )
        redis_client = getattr(persistence, "r", None)
        if redis_client is not None:
            try:
                redis_client.ping()
            except Exception as exc:
                raise HTTPException(status_code=503, detail=f"redis unavailable: {exc}")
        return {"status": "ready", "service": "automation"}

    @app.get("/metrics")
    def metrics() -> Response:
        return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)

    @app.get("/v1/templates", response_model=list[TemplateDefinition])
    def list_templates() -> list[TemplateDefinition]:
        return list(TEMPLATES)

    @app.post("/v1/requests/preview", response_model=PreviewResponse, response_model_exclude_none=True)
    def preview(intent: AutomationIntent) -> PreviewResponse:
        if policy_manager is None:
            raise HTTPException(
                status_code=503,
                detail="policy manager unavailable",
            )
        compiled = _compile_preview(intent, policy_manager.active)
        persistence.save_preview(compiled)
        return compiled

    @app.post(
        "/v1/jobs",
        response_model=JobRecord,
        response_model_exclude_none=True,
        status_code=status.HTTP_202_ACCEPTED,
    )
    def submit_job(
        submission: JobSubmission,
        idempotency_key: str = Header(alias="Idempotency-Key", min_length=1, max_length=200),
    ) -> JobRecord:
        if not submission.confirmed:
            raise HTTPException(status_code=422, detail="confirmed must be true")
        compiled = persistence.get_preview(submission.preview_id)
        if compiled is None:
            raise HTTPException(status_code=404, detail="preview not found or expired")
        return persistence.enqueue(compiled, idempotency_key)

    @app.get("/v1/jobs", response_model=list[JobRecord], response_model_exclude_none=True)
    def list_jobs(
        job_status: JobState | None = Query(default=None, alias="status"),
        limit: int = Query(default=100, ge=1, le=500),
    ) -> list[JobRecord]:
        return persistence.list_jobs(status=job_status, limit=limit)

    @app.get("/v1/jobs/{job_id}", response_model=JobRecord, response_model_exclude_none=True)
    def get_job(job_id: str) -> JobRecord:
        record = persistence.get_job(job_id)
        if record is None:
            raise HTTPException(status_code=404, detail="job not found")
        return record

    if policy_manager is not None and policy_authenticator is not None and gateway_simulator is not None:
        app.include_router(_policy_router(policy_manager, policy_authenticator, gateway_simulator))

    return app


def _build_default_store() -> AutomationStore:
    redis_url = os.environ.get("AUTOMATION_REDIS_URL")
    if not redis_url:
        raise RuntimeError(
            "AUTOMATION_REDIS_URL is not set. Refusing to silently fall back to "
            "InMemoryAutomationStore in production/Compose. Unit tests should call "
            "create_app(InMemoryAutomationStore()) explicitly instead."
        )
    client = redis.Redis.from_url(redis_url)
    return RedisAutomationStore(client)


def get_app() -> FastAPI:
    """Uvicorn factory entrypoint: uvicorn automation.app.main:get_app --factory

    只有 ASGI server 真正启动时才构造生产 app（含 AUTOMATION_REDIS_URL 检查）。
    import 本模块不会触发这个检查，所以 test_api.py / test_contract_alignment.py
    可以直接 import create_app / _compile_preview，不需要 Redis。
    """
    store = _build_default_store()
    if os.getenv("POLICY_ALLOW_ENV_ADMIN_KEY") == "true":
        policy_file = Path(os.environ["POLICY_FILE"])
        repository = InMemoryPolicyRepository(
            PolicyStoreDocument.model_validate_json(policy_file.read_text(encoding="utf-8"))
        )
        authenticator = PolicyAdminAuthenticator.from_environment_for_local_development()
    else:
        repository = KubernetesConfigMapPolicyRepository.from_environment()
        authenticator = PolicyAdminAuthenticator.from_file(Path(os.environ["POLICY_ADMIN_KEY_FILE"]))

    return create_app(
        store=store,
        policy_manager=PolicyManager(repository, RedisPolicyCache(store.r)),
        gateway_simulator=HttpGatewaySimulator(os.environ["GATEWAY_URL"]),
        policy_authenticator=authenticator,
    )
