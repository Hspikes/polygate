"""
Exact + lightly-normalized Redis cache. Owned by A.
Normalization rule is a shared contract (see contracts/README.md).

P1 fix (reported by C during Prometheus/Grafana rollout): the cache used to
attempt a Redis connection exactly once in __init__ and give up forever if
that attempt failed or if Redis restarted afterwards. This module now retries
lazily, with a short backoff, and never lets a Redis failure surface as a
500 to the caller.
"""
import hashlib
import json
import logging
import os
import threading
import time
from urllib.parse import urlsplit

try:
    import redis  # type: ignore
except ImportError:  # keeps `web`/`A` able to import without redis installed
    redis = None

log = logging.getLogger("polygate.cache")

RECONNECT_BACKOFF_SECONDS = float(os.environ.get("REDIS_RECONNECT_BACKOFF_SECONDS", "2"))


def normalize(messages: list[dict]) -> list[dict]:
    # P0 rule: strip whitespace, preserve role/order/case. Extend here + update contracts/README.md.
    return [{"role": m["role"], "content": m["content"].strip()} for m in messages]


def cache_key(
    messages: list[dict],
    privacy: str,
    scope: str = "auto",
    quality: str = "",
    max_cost_usd: float = 0.0,
    latency_target_ms: int = 0,
    policy_version: int = 1,
) -> str:
    """
    scope: 区分"自动路由"和"强制指定某个 provider"的缓存空间。
    quality / max_cost_usd / latency_target_ms: 自动路由时这些约束会影响 select_provider 的结果，
        必须纳入 key，否则改约束但 messages 没变时会被缓存短路。
    policy_version: Task 6 — active policy 的版本号。策略发布后路由结果可能变化，
        必须纳入 key，v4 缓存不能被 v5 请求命中；rollback 产生新版本号也不会错误复用旧缓存。
    """
    payload = (
        json.dumps(normalize(messages), ensure_ascii=False)
        + "|" + privacy
        + "|" + scope
        + "|" + quality
        + "|" + str(max_cost_usd)
        + "|" + str(latency_target_ms)
        + "|" + str(policy_version)
    )
    return "pg:" + hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _safe_target(url: str) -> str:
    """只返回 host:port，绝不把带密码的完整连接串写进日志。"""
    try:
        parts = urlsplit(url)
        return f"{parts.hostname}:{parts.port}"
    except Exception:
        return "<redis>"


class Cache:
    """
    带懒重连的 Redis 轻量封装。

    设计要点（P1 修复）：
    - 保存 REDIS_URL，不再只在 __init__ 里用一次。
    - _get_client() 在没有可用连接时会尝试（重新）连接，带一个短退避窗口，
      避免 Redis 挂掉时每个请求都发起一次新连接。
    - 用锁保护重连路径：FastAPI 的同步 handler 跑在线程池里，多个请求线程
      可能同时发现"没有连接"，锁避免它们同时各建一个 client。
    - get()/set() 遇到 redis.RedisError 时，绝不让异常冒泡成 500：丢弃失效的
      client（下次调用会重新连接），本次请求降级为 cache miss。
    - 任何可能包含密码、完整连接串、用户 prompt 内容的东西，都不会被打印到
      日志——只打印 host:port 和异常类型名。
    """

    def __init__(self):
        self._url = os.environ.get("REDIS_URL", "redis://localhost:6379/0")
        self._r = None
        self._lock = threading.Lock()
        # 用 None 表示"从未尝试过连接"，不能用 0.0 —— 因为 time.monotonic()
        # 在某些场景下（比如测试用的假时钟）真的会从 0.0 开始计时，
        # 如果用 0.0 当哨兵值，会跟一次真实发生在 t=0 的尝试混淆，
        # 导致退避窗口判断失效（这是写回归测试时实际测出来的 bug）。
        self._last_attempt: float | None = None
        # 启动时主动尝试一次，这样"Redis 已经 Ready"的常见情况不用等到
        # 第一个真实请求才建立连接。
        self._get_client()

    def _connect(self):
        """调用前必须持有 self._lock。"""
        if redis is None:
            return None
        try:
            client = redis.Redis.from_url(
                self._url,
                socket_connect_timeout=0.5,
                socket_timeout=0.5,
                decode_responses=True,
            )
            client.ping()
        except Exception as e:
            log.warning(
                f"redis connect failed target={_safe_target(self._url)} error={type(e).__name__}"
            )
            return None
        log.info(f"redis connected target={_safe_target(self._url)}")
        return client

    def _get_client(self):
        """返回一个可用的 client；必要时尝试重连（受退避窗口限制）。"""
        if self._r is not None:
            return self._r

        now = time.monotonic()
        if self._last_attempt is not None and (now - self._last_attempt) < RECONNECT_BACKOFF_SECONDS:
            return None  # 还在退避窗口内，不要对着挂掉的 Redis 狂发连接请求

        with self._lock:
            # 等锁的时候，可能已经有别的线程重连成功了
            if self._r is not None:
                return self._r
            now = time.monotonic()
            if self._last_attempt is not None and (now - self._last_attempt) < RECONNECT_BACKOFF_SECONDS:
                return None
            self._last_attempt = now
            self._r = self._connect()
            return self._r

    def _invalidate(self, err: Exception, op: str) -> None:
        """丢弃当前失效的 client，下次调用会重新尝试连接。"""
        log.warning(f"redis {op} failed, dropping connection error={type(err).__name__}")
        self._r = None

    @property
    def enabled(self) -> bool:
        """反映*当前*可用性——Redis 恢复后会自动变回 True。"""
        return self._get_client() is not None

    def get(self, key: str):
        client = self._get_client()
        if not client:
            return None
        try:
            raw = client.get(key)
        except redis.RedisError as e:
            self._invalidate(e, "GET")
            return None
        if not raw:
            return None
        try:
            return json.loads(raw)
        except (TypeError, ValueError) as e:
            # 缓存里的数据本身损坏，不是连接问题，不用丢弃 client
            log.warning(f"redis GET returned unparsable value error={type(e).__name__}")
            return None

    def set(self, key: str, value: dict, ttl: int = 3600):
        client = self._get_client()
        if not client:
            return
        try:
            client.set(key, json.dumps(value, ensure_ascii=False), ex=ttl)
        except redis.RedisError as e:
            self._invalidate(e, "SET")
