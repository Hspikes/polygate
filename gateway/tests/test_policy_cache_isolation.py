"""
Task 6: end-to-end regression — a change in the active policy version must
not let a cached answer leak across versions, even when messages and all
other polygate constraints are identical. Mirrors the style of
test_cache_bypass.py (real reachable Redis; predates FAKE_ADAPTER usage).

Needs a real reachable Redis (docker compose up), same as test_cache_bypass.py.
"""
import os
from pathlib import Path
import unittest
from unittest.mock import patch

PROJECT_ROOT = Path(__file__).resolve().parents[2]
os.environ["PROVIDERS_FILE"] = str(PROJECT_ROOT / "contracts" / "providers.yaml")
os.environ["FAKE_ADAPTER"] = "1"
os.environ["REDIS_URL"] = "redis://127.0.0.1:6379/0"  # real reachable Redis

from fastapi.testclient import TestClient  # noqa: E402

from app.main import CACHE, POLICY_RUNTIME, app  # noqa: E402
from app.policy import GatewayPolicySnapshot, GatewayRoutingPolicy  # noqa: E402

client = TestClient(app)


def _snapshot(version: int) -> GatewayPolicySnapshot:
    return GatewayPolicySnapshot(
        version=version,
        gateway=GatewayRoutingPolicy(
            assumed_output_tokens=256,
            balanced_price_tolerance=0.2,
            budget_mode="soft",
            latency_mode="soft",
            high_quality_strategy="prefer_real",
        ),
    )


class PolicyCacheIsolationTests(unittest.TestCase):
    def setUp(self):
        if CACHE.enabled:
            CACHE._r.flushdb()
        else:
            self.skipTest("Redis 不可达，跳过缓存相关回归测试（请确认 docker compose up 已启动）")

    def _post(self, content: str):
        return client.post(
            "/v1/chat/completions",
            json={
                "model": "auto",
                "messages": [{"role": "user", "content": content}],
                "polygate": {"privacy": "standard", "quality": "balanced"},
            },
        )

    def test_policy_version_change_bypasses_stale_cache(self):
        """同样的 messages 和约束，第一次在 policy v1 下预热缓存；
        第二次切到 policy v2，预期不会命中 v1 的缓存。"""
        with patch.object(POLICY_RUNTIME, "snapshot", return_value=_snapshot(1)):
            first = self._post("policy 版本隔离测试专用内容")
        self.assertEqual(first.status_code, 200)
        self.assertFalse(first.json()["polygate"]["cache_hit"])

        with patch.object(POLICY_RUNTIME, "snapshot", return_value=_snapshot(2)):
            second = self._post("policy 版本隔离测试专用内容")
        self.assertEqual(second.status_code, 200)
        self.assertFalse(
            second.json()["polygate"]["cache_hit"],
            "policy_version 变了但被缓存短路了！cache_key 没有正确纳入 policy_version",
        )

    def test_same_policy_version_still_hits_cache(self):
        """同样的 messages、约束和 policy version，第二次应该正常命中缓存
        （证明这不是"缓存整体坏了"，而是精确地按 policy_version 隔离）。"""
        with patch.object(POLICY_RUNTIME, "snapshot", return_value=_snapshot(1)):
            first = self._post("policy 相同版本命中测试专用内容")
            self.assertEqual(first.status_code, 200)
            self.assertFalse(first.json()["polygate"]["cache_hit"])

            second = self._post("policy 相同版本命中测试专用内容")
            self.assertEqual(second.status_code, 200)
            self.assertTrue(second.json()["polygate"]["cache_hit"])


if __name__ == "__main__":
    unittest.main()