"""
回归测试：确保 forced-provider 的存在性 + 隐私校验发生在 cache lookup 之前，
不会被缓存"抢跑"绕过。对应 code review 意见 @V。

注意：这个测试需要一个真的能连上的 Redis（跟 test_metrics.py 故意用不可达
Redis 地址不同），因为我们要测的正是"缓存命中之后，安全校验还生不生效"。
本地跑 `docker compose up` 之后，6379 端口应该有 Redis 在监听。
"""
import os
from pathlib import Path
import unittest

PROJECT_ROOT = Path(__file__).resolve().parents[2]
os.environ["PROVIDERS_FILE"] = str(PROJECT_ROOT / "contracts" / "providers.yaml")
os.environ["FAKE_ADAPTER"] = "1"
os.environ["REDIS_URL"] = "redis://127.0.0.1:6379/0"  # 真实可达的 Redis，跟 test_metrics.py 不同

from fastapi.testclient import TestClient  # noqa: E402
from app.main import CACHE, app  # noqa: E402

client = TestClient(app)


class CacheBypassRegressionTests(unittest.TestCase):
    def setUp(self):
        # 每个测试前清空缓存，避免测试之间互相污染
        if CACHE.enabled:
            CACHE._r.flushdb()
        else:
            self.skipTest("Redis 不可达，跳过缓存相关回归测试（请确认 docker compose up 已启动）")

    def _post(self, model, privacy="standard", content="回归测试用例，内容保持一致"):
        return client.post("/v1/chat/completions", json={
            "model": model,
            "messages": [{"role": "user", "content": content}],
            "polygate": {"privacy": privacy},
        })

    def test_unknown_provider_still_400_after_cache_warm(self):
        """先用 model=auto 跑一次让缓存预热，再用同样内容 + 一个不存在的 provider 名字，
        预期依然是 400，而不是被缓存放行成 200。"""
        warm = self._post("auto")
        self.assertEqual(warm.status_code, 200)

        resp = self._post("does-not-exist")
        self.assertEqual(resp.status_code, 400)
        self.assertIn("未知的 provider", resp.json()["detail"])

    def test_high_privacy_external_still_403_after_cache_warm(self):
        """先用 privacy=standard + model=auto 跑一次预热缓存，
        再用同样的 messages、但 privacy=high + 强制指定外部 provider real-a，
        预期依然是 403，而不是被缓存放行。"""
        warm = self._post("auto", privacy="standard")
        self.assertEqual(warm.status_code, 200)

        resp = self._post("real-a", privacy="high")
        self.assertEqual(resp.status_code, 403)
        self.assertIn("privacy=high", resp.json()["detail"])

    def test_forced_provider_not_polluted_by_other_providers_cache(self):
        """强制指定 mock-a 跑一次并缓存；再用同样内容强制指定 mock-b，
        预期真的调用了 mock-b（cache_hit=False），而不是被 mock-a 的缓存污染。"""
        first = self._post("mock-a")
        self.assertEqual(first.status_code, 200)
        self.assertFalse(first.json()["polygate"]["cache_hit"])
        self.assertEqual(first.json()["polygate"]["chosen_provider"], "mock-a")

        second = self._post("mock-b")
        self.assertEqual(second.status_code, 200)
        self.assertFalse(second.json()["polygate"]["cache_hit"])
        self.assertEqual(second.json()["polygate"]["chosen_provider"], "mock-b")

    def test_forced_provider_cache_still_works_for_repeated_identical_request(self):
        """同一个 provider、同样内容重复请求两次，第二次应该命中缓存（cache_hit=True），
        说明 forced 场景下缓存依然生效，只是不会被别的 provider 污染。"""
        first = self._post("mock-a", content="重复请求测试专用内容")
        self.assertEqual(first.status_code, 200)
        self.assertFalse(first.json()["polygate"]["cache_hit"])

        second = self._post("mock-a", content="重复请求测试专用内容")
        self.assertEqual(second.status_code, 200)
        self.assertTrue(second.json()["polygate"]["cache_hit"])

    def test_changing_quality_bypasses_stale_cache(self):
        """同样的 messages，先用 quality=cheap 跑一次，再改成 quality=high，
        预期两次都是真实路由（cache_hit=False），而不是第二次被第一次的缓存短路。
        这是今天在容器化环境里实测发现的 bug：cache_key 之前没有纳入 quality 参数。"""
        cheap_resp = client.post("/v1/chat/completions", json={
            "model": "auto",
            "messages": [{"role": "user", "content": "quality 切换测试专用内容"}],
            "polygate": {"privacy": "standard", "quality": "cheap"},
        })
        self.assertEqual(cheap_resp.status_code, 200)
        cheap_card = cheap_resp.json()["polygate"]
        self.assertFalse(cheap_card["cache_hit"])

        high_resp = client.post("/v1/chat/completions", json={
            "model": "auto",
            "messages": [{"role": "user", "content": "quality 切换测试专用内容"}],
            "polygate": {"privacy": "standard", "quality": "high"},
        })
        self.assertEqual(high_resp.status_code, 200)
        high_card = high_resp.json()["polygate"]

        # 核心断言：quality 变了，就算 messages 一样，也不应该命中上一次的缓存
        self.assertFalse(
            high_card["cache_hit"],
            "quality 变了但被缓存短路了！cache_key 没有正确纳入 quality 参数"
        )


if __name__ == "__main__":
    unittest.main()
