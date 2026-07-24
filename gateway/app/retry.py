"""
重试逻:指数退避(exponential backoff)+ 与熔断器联动。

"指数退避"是什么意思：
    第一次失败等 0.2 秒再试，第二次失败等 0.4 秒，第三次等 0.8 秒……
    每次等待时间翻倍。这样如果 provider 只是短暂抖动，很快就能恢复；
    但如果它是真的挂了，我们也不会在短时间内疯狂骚扰它。

只有"暂时性"错误才重试:网络超时、连接失败、5xx，以及 408/409/429。
其他 4xx 不重试；其中请求型 400/422 也不会污染 Provider 熔断状态。
"""
import asyncio
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
import random
import time

import httpx

from app.adapters import (
    AdapterResult,
    OpenedProviderStream,
    ProviderPayloadError,
    call_provider,
    open_provider_stream,
)
from app.circuit_breaker import CircuitBreakerRegistry
from app.metrics import record_provider_retry


class ProviderUnavailableError(Exception):
    """熔断器判定该 provider 当前不可用(Open 状态，或半开探测名额已被占用）。"""


class RetryBudgetExceededError(TimeoutError):
    """The next provider attempt cannot fit in the request's retry budget."""

    def __init__(self, provider_name: str):
        super().__init__(f"{provider_name} retry budget exhausted")
        self.provider_name = provider_name
        self.polygate_retries = 0


def _attach_retry_count(exc: Exception, retries: int) -> Exception:
    """Annotate a terminal provider error for request-level accounting."""
    try:
        setattr(exc, "polygate_retries", retries)
    except (AttributeError, TypeError):
        pass
    return exc


def _is_transient_error(exc: Exception) -> bool:
    """判断这个错误是不是"暂时性"的，值得重试。"""
    if isinstance(exc, httpx.TransportError):
        return True
    if isinstance(exc, httpx.HTTPStatusError):
        status = exc.response.status_code
        return status >= 500 or status in {408, 409, 429}
    return False


def _retry_reason(exc: Exception) -> str:
    """Map retryable failures to a fixed, low-cardinality reason enum."""
    if isinstance(exc, httpx.TimeoutException):
        return "timeout"
    if isinstance(exc, httpx.TransportError):
        return "transport"
    if isinstance(exc, httpx.HTTPStatusError):
        status = exc.response.status_code
        if status == 408:
            return "timeout"
        if status == 429:
            return "429"
        if status >= 500:
            return "5xx"
    return "other"


def _is_provider_failure(exc: Exception) -> bool:
    """Exclude request/adapter incompatibilities from provider health."""
    if isinstance(exc, ProviderPayloadError):
        return False
    if isinstance(exc, httpx.HTTPStatusError):
        status = exc.response.status_code
        # 400/422 are normally request-specific. Authentication, missing model,
        # throttling and server errors indicate an unusable provider/config.
        return status >= 500 or status in {401, 403, 404, 408, 409, 429}
    return True


def _record_terminal_failure(
    breaker: CircuitBreakerRegistry,
    provider_name: str,
    exc: Exception,
) -> None:
    if _is_provider_failure(exc):
        breaker.record_failure(provider_name)
    else:
        breaker.record_cancelled(provider_name)


def _retry_after_seconds(exc: Exception) -> float | None:
    """Parse Retry-After seconds or an HTTP date from an upstream response."""
    if not isinstance(exc, httpx.HTTPStatusError):
        return None
    raw = exc.response.headers.get("retry-after")
    if raw is None:
        return None
    try:
        return max(0.0, float(raw))
    except ValueError:
        try:
            retry_at = parsedate_to_datetime(raw)
        except (TypeError, ValueError, OverflowError):
            return None
        if retry_at.tzinfo is None:
            retry_at = retry_at.replace(tzinfo=UTC)
        return max(0.0, (retry_at - datetime.now(UTC)).total_seconds())


def _retry_delay(
    exc: Exception,
    attempt: int,
    base_delay_s: float,
    max_backoff_s: float,
) -> float:
    exponential = base_delay_s * (2 ** attempt)
    jitter = random.uniform(0, base_delay_s)
    backoff = min(max_backoff_s, exponential + jitter)
    retry_after = _retry_after_seconds(exc)
    return max(backoff, retry_after or 0.0)


def _remaining_seconds(deadline: float | None, clock) -> float | None:
    if deadline is None:
        return None
    return deadline - clock()


def _attempt_timeout(
    provider_name: str,
    timeout_s: float,
    deadline: float | None,
    clock,
) -> float:
    remaining = _remaining_seconds(deadline, clock)
    if remaining is None:
        return timeout_s
    if remaining <= 0:
        raise RetryBudgetExceededError(provider_name)
    return min(timeout_s, remaining)


def _ensure_delay_fits_budget(
    provider_name: str,
    delay: float,
    deadline: float | None,
    clock,
) -> None:
    remaining = _remaining_seconds(deadline, clock)
    if remaining is not None and delay >= remaining:
        raise RetryBudgetExceededError(provider_name)


def call_provider_with_resilience(
    provider: dict,
    payload: dict,
    breaker: CircuitBreakerRegistry,
    timeout_s: float = 10.0,
    max_retries: int = 2,
    base_delay_s: float = 0.2,
    max_backoff_s: float = 5.0,
    deadline: float | None = None,
    clock=time.monotonic,
    sleeper=time.sleep,
    provider_call=call_provider,
) -> AdapterResult:
    """
    包裹了 adapters.call_provider() 的"加固版"调用：

    1. 先问熔断器"这个 provider 现在能不能打"——不能就直接抛错,router 好去选下一个
    2. 能打的话，正式发请求；失败了看是不是暂时性错误，是的话按退避时间重试
    3. 无论最终成功还是失败，都告诉熔断器一声，让它更新状态
    """
    name = provider["name"]

    if not breaker.allow_request(name):
        raise ProviderUnavailableError(f"{name} 当前处于熔断状态，暂时跳过")

    last_exc: Exception | None = None
    for attempt in range(max_retries + 1):
        try:
            attempt_timeout = _attempt_timeout(name, timeout_s, deadline, clock)
            result = provider_call(provider, payload, timeout_s=attempt_timeout)
            breaker.record_success(name)
            result.retries = attempt  # 记录这次成功之前，一共重试了几次
            return result
        except Exception as exc:
            if isinstance(exc, RetryBudgetExceededError):
                _attach_retry_count(exc, attempt)
                raise
            last_exc = exc
            is_last_attempt = attempt == max_retries
            if not _is_transient_error(exc) or is_last_attempt:
                _record_terminal_failure(breaker, name, exc)
                _attach_retry_count(exc, attempt)
                raise
            delay = _retry_delay(exc, attempt, base_delay_s, max_backoff_s)
            try:
                _ensure_delay_fits_budget(name, delay, deadline, clock)
            except RetryBudgetExceededError:
                _record_terminal_failure(breaker, name, exc)
                budget_error = RetryBudgetExceededError(name)
                _attach_retry_count(budget_error, attempt)
                raise budget_error from exc
            record_provider_retry(name, _retry_reason(exc))
            sleeper(delay)

    # 理论上走不到这里，保险起见还是记一次失败再抛出
    breaker.record_failure(name)
    raise last_exc


async def open_provider_stream_with_resilience(
    provider: dict,
    payload: dict,
    breaker: CircuitBreakerRegistry,
    timeout_s: float = 90.0,
    max_retries: int = 2,
    base_delay_s: float = 0.2,
    max_backoff_s: float = 5.0,
    deadline: float | None = None,
    clock=time.monotonic,
    sleeper=asyncio.sleep,
) -> OpenedProviderStream:
    """Open and prefetch an SSE stream with retry before downstream commit."""
    name = provider["name"]
    if not breaker.allow_request(name):
        raise ProviderUnavailableError(f"{name} 当前处于熔断状态，暂时跳过")

    last_exc: Exception | None = None
    for attempt in range(max_retries + 1):
        try:
            remaining = _remaining_seconds(deadline, clock)
            if remaining is not None and remaining <= 0:
                raise RetryBudgetExceededError(name)
            opening = open_provider_stream(provider, payload, timeout_s=timeout_s)
            try:
                opened = (
                    await opening
                    if remaining is None
                    else await asyncio.wait_for(opening, timeout=remaining)
                )
            except TimeoutError as exc:
                _record_terminal_failure(breaker, name, exc)
                budget_error = RetryBudgetExceededError(name)
                _attach_retry_count(budget_error, attempt)
                raise budget_error from exc
            # The first chunk only proves that the stream opened. Do not clear
            # breaker history until the downstream consumer observes a clean
            # finish_reason followed by data: [DONE].
            opened.retries = attempt
            return opened
        except Exception as exc:
            if isinstance(exc, RetryBudgetExceededError):
                _attach_retry_count(exc, attempt)
                raise
            last_exc = exc
            is_last_attempt = attempt == max_retries
            if not _is_transient_error(exc) or is_last_attempt:
                _record_terminal_failure(breaker, name, exc)
                _attach_retry_count(exc, attempt)
                raise
            delay = _retry_delay(exc, attempt, base_delay_s, max_backoff_s)
            try:
                _ensure_delay_fits_budget(name, delay, deadline, clock)
            except RetryBudgetExceededError:
                _record_terminal_failure(breaker, name, exc)
                budget_error = RetryBudgetExceededError(name)
                _attach_retry_count(budget_error, attempt)
                raise budget_error from exc
            record_provider_retry(name, _retry_reason(exc))
            await sleeper(delay)

    breaker.record_failure(name)
    assert last_exc is not None
    raise last_exc
