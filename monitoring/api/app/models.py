"""Stable response models shared with the future monitoring frontend."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


Window = Literal["5m", "15m", "1h", "6h"]


class GatewayMetrics(BaseModel):
    requests_total: int = Field(ge=0)
    requests_per_second: float = Field(ge=0)
    error_rate: float = Field(ge=0, le=1)
    p95_latency_ms: float | None = Field(default=None, ge=0)


class CacheMetrics(BaseModel):
    lookups_total: int = Field(ge=0)
    hit_rate: float = Field(ge=0, le=1)


class UsageMetrics(BaseModel):
    input_tokens: int = Field(ge=0)
    output_tokens: int = Field(ge=0)
    estimated_cost_usd: float = Field(ge=0)


class ProviderMetrics(BaseModel):
    name: str
    requests: int = Field(ge=0)
    success_rate: float = Field(ge=0, le=1)
    p95_latency_ms: float | None = Field(default=None, ge=0)


class ResourceMetrics(BaseModel):
    # Kubernetes resource queries are added in the cloud-monitoring stage.
    available: bool = False
    cpu_cores: float | None = Field(default=None, ge=0)
    memory_bytes: int | None = Field(default=None, ge=0)
    current_replicas: int | None = Field(default=None, ge=0)
    desired_replicas: int | None = Field(default=None, ge=0)


class MonitoringOverview(BaseModel):
    generated_at: datetime
    window: Window
    gateway: GatewayMetrics
    cache: CacheMetrics
    usage: UsageMetrics
    providers: list[ProviderMetrics]
    resources: ResourceMetrics = Field(default_factory=ResourceMetrics)
    partial: bool = False
    warnings: list[str] = Field(default_factory=list)


class HealthResponse(BaseModel):
    status: Literal["ok", "degraded"]
    prometheus_reachable: bool
