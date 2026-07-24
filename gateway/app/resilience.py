"""Validated reliability settings for provider calls.

The latency target in a public request is a routing preference, not a hard
execution deadline. These server-side budgets cap retries and failover so one
Agent turn cannot occupy a worker indefinitely when providers are degraded.
"""
from __future__ import annotations

from dataclasses import dataclass
import math
import os


def _float_setting(name: str, default: float, *, minimum: float = 0.0) -> float:
    raw = os.environ.get(name)
    try:
        value = default if raw is None else float(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be a number") from exc
    if not math.isfinite(value):
        raise ValueError(f"{name} must be finite")
    if value <= minimum:
        raise ValueError(f"{name} must be greater than {minimum}")
    return value


def _non_negative_float_setting(name: str, default: float) -> float:
    raw = os.environ.get(name)
    try:
        value = default if raw is None else float(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be a number") from exc
    if not math.isfinite(value):
        raise ValueError(f"{name} must be finite")
    if value < 0:
        raise ValueError(f"{name} must be non-negative")
    return value


def _int_setting(
    name: str,
    default: int,
    *,
    minimum: int = 0,
    maximum: int = 10,
) -> int:
    raw = os.environ.get(name)
    try:
        value = default if raw is None else int(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer") from exc
    if not minimum <= value <= maximum:
        raise ValueError(f"{name} must be between {minimum} and {maximum}")
    return value


@dataclass(frozen=True)
class ResilienceSettings:
    """Provider retry and request-budget settings loaded at process startup."""

    provider_timeout_seconds: float = 10.0
    stream_idle_timeout_seconds: float = 90.0
    non_stream_budget_seconds: float = 30.0
    stream_start_budget_seconds: float = 30.0
    max_retries: int = 2
    retry_base_delay_seconds: float = 0.2
    retry_max_backoff_seconds: float = 5.0

    @classmethod
    def from_env(cls) -> "ResilienceSettings":
        return cls(
            provider_timeout_seconds=_float_setting(
                "PROVIDER_TIMEOUT_SECONDS", 10.0
            ),
            stream_idle_timeout_seconds=_float_setting(
                "PROVIDER_STREAM_IDLE_TIMEOUT_SECONDS", 90.0
            ),
            non_stream_budget_seconds=_float_setting(
                "GATEWAY_NON_STREAM_BUDGET_SECONDS", 30.0
            ),
            stream_start_budget_seconds=_float_setting(
                "GATEWAY_STREAM_START_BUDGET_SECONDS", 30.0
            ),
            max_retries=_int_setting("PROVIDER_MAX_RETRIES", 2),
            retry_base_delay_seconds=_non_negative_float_setting(
                "PROVIDER_RETRY_BASE_DELAY_SECONDS", 0.2
            ),
            retry_max_backoff_seconds=_non_negative_float_setting(
                "PROVIDER_RETRY_MAX_BACKOFF_SECONDS", 5.0
            ),
        )
