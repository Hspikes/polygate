"""
重试逻:指数退避(exponential backoff)+ 与熔断器联动。

"指数退避"是什么意思：
    第一次失败等 0.2 秒再试，第二次失败等 0.4 秒，第三次等 0.8 秒……
    每次等待时间翻倍。这样如果 provider 只是短暂抖动，很快就能恢复；
    但如果它是真的挂了，我们也不会在短时间内疯狂骚扰它。

只有"暂时性"错误才重试:网络超时、连接失败、5xx(服务器端错误,比如 500/502/503)。
4xx(比如 401 密钥错误、400 参数错误）不重试——这类错误重试也没用，
只会白白拖慢用户等待的时间。
"""
import time
import random
import httpx

from app.adapters import call_provider, AdapterResult
from app.circuit_breaker import CircuitBreakerRegistry


class ProviderUnavailableError(Exception):
    """熔断器判定该 provider 当前不可用(Open 状态，或半开探测名额已被占用）。"""


def _is_transient_error(exc: Exception) -> bool:
    """判断这个错误是不是"暂时性"的，值得重试。"""
    if isinstance(exc, (httpx.TimeoutException, httpx.ConnectError)):
        return True
    if isinstance(exc, httpx.HTTPStatusError):
        status = exc.response.status_code
        return status >= 500  # 5xx 是服务端问题，可能重试就好了；4xx 是请求本身有问题
    return False


def call_provider_with_resilience(
    provider: dict,
    messages: list[dict],
    breaker: CircuitBreakerRegistry,
    timeout_s: float = 10.0,
    max_retries: int = 2,
    base_delay_s: float = 0.2,
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
            result = call_provider(provider, messages, timeout_s=timeout_s)
            breaker.record_success(name)
            result.retries = attempt  # 记录这次成功之前，一共重试了几次
            return result
        except Exception as exc:
            last_exc = exc
            is_last_attempt = attempt == max_retries
            if not _is_transient_error(exc) or is_last_attempt:
                breaker.record_failure(name)
                raise
            delay = base_delay_s * (2 ** attempt) + random.uniform(0, base_delay_s)
            time.sleep(delay)

    # 理论上走不到这里，保险起见还是记一次失败再抛出
    breaker.record_failure(name)
    raise last_exc