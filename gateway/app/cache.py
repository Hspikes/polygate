"""Exact + lightly-normalized Redis cache. Owned by A. Normalization rule is a shared contract (see contracts/README.md)."""
import hashlib
import json
import os

try:
    import redis  # type: ignore
except ImportError:  # keeps `web`/`A` able to import without redis installed
    redis = None


def normalize(messages: list[dict]) -> list[dict]:
    # P0 rule: strip whitespace, preserve role/order/case. Extend here + update contracts/README.md.
    return [{"role": m["role"], "content": m["content"].strip()} for m in messages]


def cache_key(messages: list[dict], privacy: str, scope: str = "auto") -> str:
    """
    scope: 区分"自动路由"和"强制指定某个 provider"的缓存空间。
    - scope="auto"        自动路由请求共享同一份缓存
    - scope="<provider名>" 强制指定该 provider 的请求，只和同样强制指定该 provider 的请求共享缓存
    这样可以避免：强制指定 provider-B 的请求，被 provider-A 的历史缓存"张冠李戴"。
    """
    payload = json.dumps(normalize(messages), ensure_ascii=False) + "|" + privacy + "|" + scope
    return "pg:" + hashlib.sha256(payload.encode("utf-8")).hexdigest()

class Cache:
    """Thin wrapper; degrades to a no-op if Redis is unreachable so local dev never blocks."""

    def __init__(self):
        self._r = None
        url = os.environ.get("REDIS_URL", "redis://localhost:6379/0")
        if redis is not None:
            try:
                self._r = redis.Redis.from_url(url, socket_connect_timeout=0.5, decode_responses=True)
                self._r.ping()
            except Exception:
                self._r = None  # fall back to disabled

    @property
    def enabled(self) -> bool:
        return self._r is not None

    def get(self, key: str):
        if not self._r:
            return None
        try:
            raw = self._r.get(key)
            return json.loads(raw) if raw else None
        except Exception:
            return None

    def set(self, key: str, value: dict, ttl: int = 3600):
        if not self._r:
            return
        try:
            self._r.set(key, json.dumps(value, ensure_ascii=False), ex=ttl)
        except Exception:
            pass
