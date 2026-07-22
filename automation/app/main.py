from __future__ import annotations

import json
import shlex
import uuid

from fastapi import FastAPI, Header, HTTPException, Query, status

from automation.app.models import (
    AutomationIntent,
    GatewayMessage,
    GatewayRequest,
    JobRecord,
    JobSubmission,
    JobState,
    PreviewResponse,
    PriorityDecision,
    Snippets,
    TemplateDefinition,
)
from automation.app.store import AutomationStore, InMemoryAutomationStore
from automation.app.templates import TEMPLATE_BY_SCENARIO, TEMPLATES, URGENCY_SCORE


PREVIEW_TTL_SECONDS = 600
POLYGATE_URL_DEFAULT = "http://localhost:8000"
POLYGATE_URL_PLACEHOLDER = "${POLYGATE_URL:-" + POLYGATE_URL_DEFAULT + "}"


def _compile_preview(intent: AutomationIntent) -> PreviewResponse:
    normalized = intent.model_copy(deep=True)
    adjustments: list[str] = []
    template = TEMPLATE_BY_SCENARIO[normalized.scenario]

    if normalized.scenario.value == "finance_summary" and normalized.preferences.privacy != "high":
        normalized.preferences.privacy = "high"
        adjustments.append("finance_summary requires privacy=high")

    urgency_score = URGENCY_SCORE[normalized.urgency.value]
    score = urgency_score + template.scenario_weight
    priority = PriorityDecision(
        **{
            "class": normalized.urgency,
            "initial_score": score,
            "reason": (
                f"{normalized.urgency.value} urgency ({urgency_score}) + "
                f"{normalized.scenario.value} scenario ({template.scenario_weight})"
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
    )


def create_app(store: AutomationStore | None = None) -> FastAPI:
    app = FastAPI(title="PolyGate Automation API", version="0.1.0-skeleton")
    persistence = store or InMemoryAutomationStore()

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok", "service": "automation"}

    @app.get("/v1/templates", response_model=list[TemplateDefinition])
    def list_templates() -> list[TemplateDefinition]:
        return list(TEMPLATES)

    @app.post("/v1/requests/preview", response_model=PreviewResponse)
    def preview(intent: AutomationIntent) -> PreviewResponse:
        compiled = _compile_preview(intent)
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

    return app


app = create_app()
