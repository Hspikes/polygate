"""OpenAI Chat Completions request models plus PolyGate extensions.

The gateway validates fields that affect Agent semantics instead of silently
dropping them. Provider-specific adaptation happens in ``app.adapters``.
"""
from typing import Any, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, model_validator


class Message(BaseModel):
    """The Chat Completions message surface used by Pi and the Web client."""

    model_config = ConfigDict(extra="allow")

    role: Literal["system", "developer", "user", "assistant", "tool"]
    content: str | list[dict[str, Any]] | None = None
    name: Optional[str] = None
    tool_calls: Optional[list[dict[str, Any]]] = None
    tool_call_id: Optional[str] = None

    @model_validator(mode="after")
    def validate_role_fields(self):
        if self.role == "tool" and not self.tool_call_id:
            raise ValueError("tool messages require tool_call_id")
        if self.role != "assistant" and self.content is None:
            raise ValueError(f"{self.role} messages require content")
        if self.role == "assistant" and self.content is None and not self.tool_calls:
            raise ValueError("assistant messages require content or tool_calls")
        return self


class PolygateConstraints(BaseModel):
    model_config = ConfigDict(extra="forbid")

    latency_target_ms: int = Field(default=3000, gt=0)
    max_cost_usd: float = Field(default=0.01, ge=0)
    privacy: Literal["standard", "high"] = "standard"
    quality: Literal["balanced", "high", "cheap"] = "balanced"
    cache_control: Literal["auto", "no-store"] = "auto"
    session_id: Optional[str] = Field(default=None, min_length=1, max_length=256)


class GatewayRequest(BaseModel):
    """A deliberately explicit, Agent-capable Chat Completions request.

    ``extra='forbid'`` is intentional: an unknown generation field must not be
    accepted and then discarded, because that can change Agent behaviour.
    """

    model_config = ConfigDict(extra="forbid")

    model: str = "auto"
    messages: list[Message] = Field(min_length=1)
    tools: Optional[list[dict[str, Any]]] = None
    tool_choice: Optional[str | dict[str, Any]] = None
    parallel_tool_calls: Optional[bool] = None
    temperature: Optional[float] = None
    top_p: Optional[float] = None
    max_tokens: Optional[int] = Field(default=None, gt=0)
    max_completion_tokens: Optional[int] = Field(default=None, gt=0)
    stop: Optional[str | list[str]] = None
    response_format: Optional[dict[str, Any]] = None
    stream: bool = False
    stream_options: Optional[dict[str, Any]] = None
    seed: Optional[int] = None
    frequency_penalty: Optional[float] = None
    presence_penalty: Optional[float] = None
    logit_bias: Optional[dict[str, float]] = None
    logprobs: Optional[bool] = None
    top_logprobs: Optional[int] = None
    user: Optional[str] = None
    service_tier: Optional[str] = None
    reasoning_effort: Optional[str] = None
    store: Optional[bool] = None
    polygate: PolygateConstraints = Field(default_factory=PolygateConstraints)

    @model_validator(mode="after")
    def validate_stream_options(self):
        if self.stream_options is not None and not self.stream:
            raise ValueError("stream_options requires stream=true")
        return self

    def provider_payload(self) -> dict[str, Any]:
        """Return the standard request body; never forward PolyGate policy."""
        return self.model_dump(exclude={"polygate"}, exclude_none=True)

    def message_dicts(self) -> list[dict[str, Any]]:
        return [message.model_dump(exclude_none=True) for message in self.messages]

    def required_capabilities(self) -> set[str]:
        required: set[str] = set()
        if self.stream:
            required.add("streaming")
        if self.tools or any(
            message.role == "tool" or bool(message.tool_calls)
            for message in self.messages
        ):
            required.add("tools")
        if self.parallel_tool_calls:
            required.add("parallel_tool_calls")
        if any(
            isinstance(message.content, list)
            and any(block.get("type") not in {"text", "input_text"} for block in message.content)
            for message in self.messages
        ):
            required.add("vision")
        return required

    def bypass_cache(self) -> bool:
        """Agent/tool/stream traffic cannot safely reuse P0 JSON cache items."""
        return (
            self.polygate.cache_control == "no-store"
            or self.stream
            or bool(self.tools)
            or any(
                message.role == "tool"
                or bool(message.tool_calls)
                or not isinstance(message.content, str)
                for message in self.messages
            )
        )


class Tokens(BaseModel):
    input: int
    output: int


class DecisionCard(BaseModel):
    chosen_provider: str
    reason: str
    cache_hit: bool
    cost_estimate_usd: float
    latency_ms: int
    tokens: Tokens
    retries: int = 0
    failover_from: Optional[str] = None
    request_id: str
