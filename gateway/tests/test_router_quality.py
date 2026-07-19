"""
针对 router.py 的单元测试：验证 quality=cheap / balanced / high 三种策略
在不同价格结构下，确实会给出不同的路由结果，并且 reason 诚实反映实际决策。
"""
import unittest
from types import SimpleNamespace

from app.router import select_provider


def _constraints(quality="balanced", privacy="standard", max_cost_usd=1.0, latency_target_ms=99999):
    """构造一个跟 GatewayRequest.polygate 长得一样的对象，供 select_provider 使用。"""
    return SimpleNamespace(
        quality=quality, privacy=privacy,
        max_cost_usd=max_cost_usd, latency_target_ms=latency_target_ms,
    )


MESSAGES = [{"role": "user", "content": "hello"}]


class RouterQualityPolicyTests(unittest.TestCase):
    def setUp(self):
        # 特意构造价差适中的 Provider 组合，跟 providers.yaml 里悬殊的真实价格分开，
        # 专门用来验证 balanced 的"20% 容忍度"分支逻辑。
        self.providers_close_price = [
            {"name": "real-x", "kind": "real", "privacy": "external",
             "price_per_1k_input": 0.0011, "price_per_1k_output": 0.0011,
             "typical_latency_ms": 500},
            {"name": "mock-x", "kind": "mock", "privacy": "internal",
             "price_per_1k_input": 0.001, "price_per_1k_output": 0.001,
             "typical_latency_ms": 300},
        ]
        # 价差悬殊的组合，验证"价差过大就不选 real"的分支。
        self.providers_far_price = [
            {"name": "real-y", "kind": "real", "privacy": "external",
             "price_per_1k_input": 0.01, "price_per_1k_output": 0.01,
             "typical_latency_ms": 500},
            {"name": "mock-y", "kind": "mock", "privacy": "internal",
             "price_per_1k_input": 0.0001, "price_per_1k_output": 0.0001,
             "typical_latency_ms": 300},
        ]

    def test_cheap_always_picks_cheapest(self):
        chosen, reason, _ = select_provider(self.providers_close_price, MESSAGES, _constraints(quality="cheap"))
        self.assertEqual(chosen["name"], "mock-x")

    def test_balanced_picks_real_when_price_gap_small(self):
        """价差在 20% 容忍度以内时，balanced 应该选真实 Provider，跟 cheap 结果不同。"""
        chosen, reason, _ = select_provider(self.providers_close_price, MESSAGES, _constraints(quality="balanced"))
        self.assertEqual(chosen["name"], "real-x")
        self.assertIn("价差在", reason)

    def test_balanced_picks_cheapest_when_price_gap_large(self):
        """价差过大时，balanced 应该跟 cheap 一样选最便宜的。"""
        chosen, reason, _ = select_provider(self.providers_far_price, MESSAGES, _constraints(quality="balanced"))
        self.assertEqual(chosen["name"], "mock-y")
        self.assertIn("价差过大", reason)

    def test_high_prefers_real_when_available(self):
        chosen, reason, _ = select_provider(self.providers_close_price, MESSAGES, _constraints(quality="high"))
        self.assertEqual(chosen["name"], "real-x")
        self.assertIn("优先真实 Provider", reason)

    def test_high_reason_is_honest_when_privacy_excludes_all_real(self):
        """privacy=high 把唯一的 real 过滤掉之后，quality=high 的 reason 不应该继续宣称
        选中了真实 Provider——这是这次修复要求的诚实性。"""
        chosen, reason, _ = select_provider(
            self.providers_close_price, MESSAGES,
            _constraints(quality="high", privacy="high"),
        )
        self.assertEqual(chosen["name"], "mock-x")  # real-x 是 external，被 privacy=high 过滤掉了
        self.assertIn("无可用真实 Provider", reason)
        self.assertNotIn("优先真实 Provider", reason)


if __name__ == "__main__":
    unittest.main()