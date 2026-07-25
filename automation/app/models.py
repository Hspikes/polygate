from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


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


class GatewaySimulationMessage(BaseModel):
    """Gateway's agent-capable message boundary used only for routing simulation."""

    model_config = ConfigDict(extra="allow")

    role: Literal["system", "developer", "user", "assistant", "tool"]
    content: str | list[dict[str, Any]] | None = None
    name: str | None = None
    tool_calls: list[dict[str, Any]] | None = None
    tool_call_id: str | None = None

    @model_validator(mode="after")
    def validate_role_fields(self) -> "GatewaySimulationMessage":
        if self.role == "tool" and not self.tool_call_id:
            raise ValueError("tool messages require tool_call_id")
        if self.role != "assistant" and self.content is None:
            raise ValueError(f"{self.role} messages require content")
        if self.role == "assistant" and self.content is None and not self.tool_calls:
            raise ValueError("assistant messages require content or tool_calls")
        return self


class GatewaySimulationConstraints(BaseModel):
    model_config = ConfigDict(extra="forbid")

    latency_target_ms: int = Field(default=3000, gt=0)
    max_cost_usd: float = Field(default=0.01, ge=0)
    privacy: Literal["standard", "high"] = "standard"
    quality: Literal["balanced", "high", "cheap"] = "balanced"
    cache_control: Literal["auto", "no-store"] = "auto"
    session_id: str | None = Field(default=None, min_length=1, max_length=256)


class GatewaySimulationRequest(BaseModel):
    """A local mirror of GatewayRequest for the `/internal/routing/simulate` boundary.

    Automation's existing GatewayRequest remains the intentionally smaller preview
    contract. This model accepts exactly the agent request features consumed by
    Gateway's internal simulation endpoint.
    """

    model_config = ConfigDict(extra="forbid")

    model: str = Field(default="auto", min_length=1)
    messages: list[GatewaySimulationMessage] = Field(min_length=1)
    tools: list[dict[str, Any]] | None = None
    tool_choice: Literal["none", "auto", "required"] | dict[str, Any] | None = None
    parallel_tool_calls: bool | None = None
    temperature: float | None = Field(default=None, ge=0, le=2)
    top_p: float | None = Field(default=None, ge=0, le=1)
    max_tokens: int | None = Field(default=None, gt=0)
    max_completion_tokens: int | None = Field(default=None, gt=0)
    stop: str | list[str] | None = None
    response_format: dict[str, Any] | None = None
    stream: bool = False
    stream_options: dict[str, Any] | None = None
    seed: int | None = None
    frequency_penalty: float | None = Field(default=None, ge=-2, le=2)
    presence_penalty: float | None = Field(default=None, ge=-2, le=2)
    logit_bias: dict[str, float] | None = None
    logprobs: bool | None = None
    top_logprobs: int | None = Field(default=None, ge=0, le=20)
    user: str | None = None
    service_tier: str | None = None
    reasoning_effort: str | None = None
    store: bool | None = None
    polygate: GatewaySimulationConstraints = Field(default_factory=GatewaySimulationConstraints)

    @model_validator(mode="after")
    def validate_gateway_options(self) -> "GatewaySimulationRequest":
        if self.stream_options is not None and not self.stream:
            raise ValueError("stream_options requires stream=true")
        if self.stream_options is not None:
            include_usage = self.stream_options.get("include_usage")
            if include_usage is not None and not isinstance(include_usage, bool):
                raise ValueError("stream_options.include_usage must be a boolean")
        if self.top_logprobs is not None and self.logprobs is not True:
            raise ValueError("top_logprobs requires logprobs=true")
        return self


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
    policy_version: int | None = Field(default=None, ge=1)


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
    policy_version: int | None = Field(default=None, ge=1)

class TemplateDefinition(StrictModel):
    id: Scenario
    label: str
    defaults: Preferences
    locked_fields: list[str]
    scenario_weight: int
