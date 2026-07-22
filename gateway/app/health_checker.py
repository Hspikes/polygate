"""
健康检查：定期主动探活每个 provider 的 /health 接口。

和"熔断器"的区别（这两个经常被搞混，说清楚一下）：
- 熔断器是"被动"的——只有真实业务请求失败时，它才会知道 provider 出问题了。
- 健康检查是"主动"的——不管有没有业务请求，都定期主动问一句"你还好吗"。
  好处是：即使某个 provider 很久没被路由选中，我们也能提前发现它已经挂了，
  而不是等用户下次请求正好选中它才发现。

两者的检测结果最终都汇总进同一个 CircuitBreakerRegistry,
这样 router.py 那边只需要认一份 health_snapshot()，完全不用关心
这个健康状态是"主动探测"出来的还是"业务请求失败"统计出来的。
"""
import asyncio
import httpx
import logging
from urllib.parse import urlsplit, urlunsplit

from app.circuit_breaker import CircuitBreakerRegistry

log = logging.getLogger("polygate.health_checker")


def _default_health_url(endpoint: str) -> str:
    """把业务 endpoint(比如 http://mock-a:8080/v1/chat/completions)
    转换成对应的健康检查地址（比如 http://mock-a:8080/health)。"""
    parts = urlsplit(endpoint)
    return urlunsplit((parts.scheme, parts.netloc, "/health", "", ""))


async def _probe_once(provider: dict, breaker: CircuitBreakerRegistry,
                       client: httpx.AsyncClient, timeout_s: float):
    name = provider["name"]
    # 优先用 provider 配置里显式写的 health_endpoint；
    # 没有的话，用业务 endpoint 自动推导出对应的 /health 地址
    url = provider.get("health_endpoint") or _default_health_url(provider.get("endpoint", ""))
    try:
        resp = await client.get(url, timeout=timeout_s)
        resp.raise_for_status()
        # 体检成功只能证明"进程还活着"，不代表真实业务请求也一定成功，
        # 所以体检成功不清空熔断记录——真正的"恢复"交给业务流量自己证明
    except Exception as exc:
        log.warning(f"health probe failed for {name}: {exc}")
        breaker.record_failure(name)


async def health_check_loop(providers: list[dict], breaker: CircuitBreakerRegistry,
                             interval_s: float = 5.0, timeout_s: float = 3.0):
    """
    后台常驻协程（会一直循环运行，不会自己结束）。

    要在 main.py 里,FastAPI 应用启动的时候用类似这样的方式启动它：

        @app.on_event("startup")
        async def startup():
            asyncio.create_task(health_check_loop(PROVIDERS, BREAKER))

    这样它就会在后台每隔 interval_s 秒探活一次所有 provider,
    不会阻塞正常处理用户请求。
    """
    async with httpx.AsyncClient() as client:
        while True:
            # 同时探活所有 provider，而不是一个一个排队问，节省时间
            await asyncio.gather(*[
                _probe_once(p, breaker, client, timeout_s) for p in providers
            ])
            await asyncio.sleep(interval_s)