"""Short-lived, redacted Decision Records backed by the existing Redis cache."""
from __future__ import annotations

import logging
import os
from datetime import datetime
from typing import Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from app.models import Tokens


log = logging.getLogger("polygate.decisions")

DECISION_KEY_PREFIX = "pg:decision:"
DEFAULT_DECISION_TTL_SECONDS = 3600
MIN_DECISION_TTL_SECONDS = 60
MAX_DECISION_TTL_SECONDS = 86400
REQUEST_ID_PATTERN = r"^req_[0-9a-f]{32}$"


class DecisionRecord(BaseModel):
    """Public, redacted result of one completed Gateway request."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1] = 1
    request_id: str = Field(pattern=REQUEST_ID_PATTERN)
    outcome: Literal["success", "cache_hit", "cancelled", "partial_error"]
    chosen_provider: str = Field(min_length=1)
    initial_provider: str = Field(min_length=1)
    reason: str = Field(min_length=1)
    cache_hit: bool
    stream: bool
    cost_estimate_usd: float = Field(ge=0)
    latency_ms: int = Field(ge=0)
    tokens: Tokens
    retries: int = Field(default=0, ge=0)
    failover_from: str | None = None
    failover_count: int = Field(default=0, ge=0)
    created_at: datetime
    expires_at: datetime

    @model_validator(mode="after")
    def validate_failover_fields(self):
        if self.failover_count == 0 and self.failover_from is not None:
            raise ValueError("failover_from requires failover_count greater than zero")
        if self.failover_count > 0 and self.failover_from is None:
            raise ValueError("failover_count requires failover_from")
        if self.created_at.utcoffset() is None or self.expires_at.utcoffset() is None:
            raise ValueError("decision record timestamps must include a timezone")
        if self.expires_at <= self.created_at:
            raise ValueError("expires_at must be later than created_at")
        return self


class CacheLike(Protocol):
    @property
    def enabled(self) -> bool: ...

    def get(self, key: str): ...

    def set(self, key: str, value: dict, ttl: int = 3600): ...


class DecisionStoreUnavailable(RuntimeError):
    """Redis is currently unavailable, so records cannot be queried."""


def _ttl_from_env() -> int:
    raw = os.environ.get(
        "DECISION_RECORD_TTL_SECONDS",
        str(DEFAULT_DECISION_TTL_SECONDS),
    )
    try:
        value = int(raw)
    except ValueError as exc:
        raise ValueError("DECISION_RECORD_TTL_SECONDS must be an integer") from exc
    if not MIN_DECISION_TTL_SECONDS <= value <= MAX_DECISION_TTL_SECONDS:
        raise ValueError(
            "DECISION_RECORD_TTL_SECONDS must be between "
            f"{MIN_DECISION_TTL_SECONDS} and {MAX_DECISION_TTL_SECONDS}"
        )
    return value


class DecisionStore:
    """Persist only validated DecisionRecord fields under a bounded Redis TTL."""

    def __init__(
        self,
        cache: CacheLike,
        *,
        ttl_seconds: int | None = None,
    ):
        self._cache = cache
        self.ttl_seconds = _ttl_from_env() if ttl_seconds is None else ttl_seconds
        if not MIN_DECISION_TTL_SECONDS <= self.ttl_seconds <= MAX_DECISION_TTL_SECONDS:
            raise ValueError(
                "decision record TTL must be between "
                f"{MIN_DECISION_TTL_SECONDS} and {MAX_DECISION_TTL_SECONDS}"
            )

    @staticmethod
    def key(request_id: str) -> str:
        return f"{DECISION_KEY_PREFIX}{request_id}"

    def save(self, record: DecisionRecord) -> bool:
        """Best-effort write; callers keep serving chat when Redis is unavailable."""
        if not self._cache.enabled:
            return False
        self._cache.set(
            self.key(record.request_id),
            record.model_dump(mode="json"),
            ttl=self.ttl_seconds,
        )
        return self._cache.enabled

    def get(self, request_id: str) -> DecisionRecord | None:
        if not self._cache.enabled:
            raise DecisionStoreUnavailable
        raw = self._cache.get(self.key(request_id))
        if raw is None:
            if not self._cache.enabled:
                raise DecisionStoreUnavailable
            return None
        try:
            return DecisionRecord.model_validate(raw)
        except ValidationError as exc:
            log.warning(
                "decision record validation failed request_id=%s error=%s",
                request_id,
                type(exc).__name__,
            )
            return None
