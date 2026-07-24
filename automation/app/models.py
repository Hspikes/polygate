from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Department(str, Enum):
    engineering = "engineering"
    support = "support"
    finance = "finance"
    marketing = "marketing"


class Scenario(str, Enum):
    production_incident = "production_incident"
    customer_escalation = "customer_escalation"
    finance_summary = "finance_summary"
    marketing_batch = "marketing_batch"


class Urgency(str, Enum):
    critical = "critical"
    high = "high"
    normal = "normal"
    low = "low"


class Preferences(StrictModel):
    quality: Literal["cheap", "balanced", "high"]
    privacy: Literal["standard", "high"]
    max_cost_usd: float = Field(ge=0, le=10)
    latency_target_ms: int = Field(ge=1, le=120_000)


class AutomationIntent(StrictModel):
    employee: str = Field(min_length=1, max_length=80)
    department: Department
    scenario: Scenario
    urgency: Urgency
    prompt: str = Field(min_length=1, max_length=20_000)
    preferences: Preferences


class PriorityDecision(StrictModel):
    class_: Urgency = Field(alias="class", serialization_alias="class")
    initial_score: int
    reason: str

    model_config = ConfigDict(extra="forbid", populate_by_name=True)


class GatewayMessage(StrictModel):
    role: str
    content: str


class GatewayRequest(StrictModel):
    model: str = "auto"
    messages: list[GatewayMessage]
    polygate: Preferences


class Snippets(StrictModel):
    json_: str = Field(alias="json", serialization_alias="json")
    curl: str
    python: str

    model_config = ConfigDict(extra="forbid", populate_by_name=True)


class PreviewResponse(StrictModel):
    preview_id: str
    expires_in_seconds: int
    normalized_intent: AutomationIntent
    priority: PriorityDecision
    gateway_request: GatewayRequest
    snippets: Snippets
    policy_adjustments: list[str]
    policy_version: int | None = None


class JobSubmission(StrictModel):
    preview_id: str
    confirmed: bool


class JobState(str, Enum):
    queued = "queued"
    running = "running"
    completed = "completed"
    failed = "failed"


class JobRecord(StrictModel):
    job_id: str
    status: JobState
    priority: PriorityDecision
    queue_position: int = Field(ge=0)
    created_at: datetime
    started_at: datetime | None = None
    completed_at: datetime | None = None
    result: dict[str, Any] | None = None
    error: str | None = None
    policy_version: int | None = None

class TemplateDefinition(StrictModel):
    id: Scenario
    label: str
    defaults: Preferences
    locked_fields: list[str]
    scenario_weight: int
