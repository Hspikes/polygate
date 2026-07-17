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


def cache_key(messages: list[dict], privacy: str) -> str:
    payload = json.dumps(normalize(messages), ensure_ascii=False) + "|" + privacy
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
