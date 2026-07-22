"""
针对 cache.py 重连逻辑的回归测试。对应 C 报告的 Gateway-Redis 启动竞态问题。

策略：用一个假的 redis 模块（FakeRedisModule）替换 app.cache.redis，
这样可以精确控制"连接成功/失败""GET/SET 抛错"这些场景，不依赖真实 Redis。
时间相关的退避逻辑通过 monkeypatch time.monotonic 精确控制。
"""
import unittest
from unittest.mock import MagicMock, patch

import app.cache as cache_module


class FakeRedisError(Exception):
    """模拟 redis.RedisError，必须是真正的 Exception 子类才能被 except 捕获到。"""
    pass


class FakeRedisModule:
    """替身模块：提供 .Redis.from_url(...) 和 .RedisError，行为完全可控。"""
    def __init__(self):
        self.RedisError = FakeRedisError
        self.Redis = MagicMock()


class FakeClock:
    """可手动推进的时钟，替换 time.monotonic。"""
    def __init__(self, start=0.0):
        self.now = start

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds


def _make_working_client():
    client = MagicMock()
    client.ping.return_value = True
    client._store = {}

    def fake_get(key):
        return client._store.get(key)

    def fake_set(key, value, ex=None):
        client._store[key] = value

    client.get.side_effect = fake_get
    client.set.side_effect = fake_set
    return client


def _make_failing_ping_client():
    client = MagicMock()
    client.ping.side_effect = FakeRedisError("connection refused")
    return client


class CacheReconnectTests(unittest.TestCase):
    def setUp(self):
        self.fake_redis = FakeRedisModule()
        self.clock = FakeClock()
        self.redis_patch = patch.object(cache_module, "redis", self.fake_redis)
        self.time_patch = patch.object(cache_module.time, "monotonic", self.clock)
        self.redis_patch.start()
        self.time_patch.start()
        self.addCleanup(self.redis_patch.stop)
        self.addCleanup(self.time_patch.stop)

    def _new_cache(self):
        with patch.dict("os.environ", {"REDIS_URL": "redis://fake-host:6379/0"}):
            return cache_module.Cache()

    def test_reconnects_after_redis_becomes_available(self):
        """启动时 Redis 连不上；恢复后，后续 get/set 应该能重新连接成功。"""
        self.fake_redis.Redis.from_url.return_value = _make_failing_ping_client()
        cache = self._new_cache()
        self.assertFalse(cache.enabled)

        # 时间推进超过退避窗口，且这次 Redis 恢复了
        self.clock.advance(cache_module.RECONNECT_BACKOFF_SECONDS + 0.1)
        self.fake_redis.Redis.from_url.return_value = _make_working_client()

        self.assertTrue(cache.enabled)

    def test_invalidates_broken_client_after_get_error(self):
        """已有连接的情况下 GET 抛错：请求降级为 miss，且失效的 client 被清除。"""
        working = _make_working_client()
        self.fake_redis.Redis.from_url.return_value = working
        cache = self._new_cache()
        self.assertTrue(cache.enabled)

        working.get.side_effect = FakeRedisError("connection lost")
        result = cache.get("some-key")
        self.assertIsNone(result)
        self.assertIsNone(cache._r)  # client 已被清除

        # 退避窗口过后，应该允许重新连接（即使还是同一个 mock，也验证流程不会卡死）
        self.clock.advance(cache_module.RECONNECT_BACKOFF_SECONDS + 0.1)
        self.fake_redis.Redis.from_url.return_value = _make_working_client()
        self.assertTrue(cache.enabled)

    def test_invalidates_broken_client_after_set_error(self):
        """SET 抛错时：网关不应该收到异常（不能变成 500），且 client 会被清除、允许后续重连。"""
        working = _make_working_client()
        self.fake_redis.Redis.from_url.return_value = working
        cache = self._new_cache()
        self.assertTrue(cache.enabled)

        working.set.side_effect = FakeRedisError("connection lost")
        try:
            cache.set("some-key", {"answer": "x", "tokens": {"input": 1, "output": 1}})
        except Exception as e:
            self.fail(f"cache.set() 不应该向调用方抛出异常，但抛出了: {e!r}")

        self.assertIsNone(cache._r)

        self.clock.advance(cache_module.RECONNECT_BACKOFF_SECONDS + 0.1)
        self.fake_redis.Redis.from_url.return_value = _make_working_client()
        self.assertTrue(cache.enabled)

    def test_cache_hit_after_reconnect(self):
        """恢复连接后：第一次 set 写入，第二次 get 相同 key 应该命中（不再需要真的调用 Provider）。"""
        self.fake_redis.Redis.from_url.return_value = _make_failing_ping_client()
        cache = self._new_cache()
        self.assertFalse(cache.enabled)

        self.clock.advance(cache_module.RECONNECT_BACKOFF_SECONDS + 0.1)
        self.fake_redis.Redis.from_url.return_value = _make_working_client()

        key = cache_module.cache_key(
            [{"role": "user", "content": "hello"}], "standard", "auto", "balanced", 0.01
        )
        self.assertIsNone(cache.get(key))  # 恢复后的第一次仍是 miss（之前没写过）

        cache.set(key, {"answer": "hi", "tokens": {"input": 1, "output": 1}})
        cached = cache.get(key)
        self.assertIsNotNone(cached)
        self.assertEqual(cached["answer"], "hi")

    def test_reconnect_backoff(self):
        """Redis 持续不可用时，退避窗口内不应该对每次调用都重新发起连接。"""
        self.fake_redis.Redis.from_url.return_value = _make_failing_ping_client()
        cache = self._new_cache()
        connect_calls_after_init = self.fake_redis.Redis.from_url.call_count
        self.assertEqual(connect_calls_after_init, 1)

        # 退避窗口内连续调用，不应该增加连接尝试次数
        cache.get("k1")
        cache.get("k2")
        self.assertEqual(self.fake_redis.Redis.from_url.call_count, connect_calls_after_init)

        # 时间推进超过退避窗口，才允许再次尝试
        self.clock.advance(cache_module.RECONNECT_BACKOFF_SECONDS + 0.1)
        cache.get("k3")
        self.assertEqual(self.fake_redis.Redis.from_url.call_count, connect_calls_after_init + 1)


if __name__ == "__main__":
    unittest.main()