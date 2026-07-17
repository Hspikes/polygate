"""Pydantic models mirroring contracts/*.schema.json. Keep in sync with the schemas."""
from typing import List, Literal, Optional
from pydantic import BaseModel, Field


class Message(BaseModel):
    role: Literal["system", "user", "assistant"]
    content: str


class PolygateConstraints(BaseModel):
    latency_target_ms: int = 3000
    max_cost_usd: float = 0.01
    privacy: Literal["standard", "high"] = "standard"
    quality: Literal["balanced", "high", "cheap"] = "balanced"


class GatewayRequest(BaseModel):
    model: str = "auto"
    messages: List[Message]
    polygate: PolygateConstraints = Field(default_factory=PolygateConstraints)


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
