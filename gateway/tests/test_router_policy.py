"""
Task 5, Step 3: failing tests for the parameterized select_provider(..., policy=...).
Written before Step 4 changes select_provider's signature — expected to fail now.
"""
import unittest
from types import SimpleNamespace

from app.policy import GatewayRoutingPolicy
from app.router import select_provider


def _constraints(quality="balanced", privacy="standard", max_cost_usd=1.0, latency_target_ms=99999):
    return SimpleNamespace(
        quality=quality, privacy=privacy,
        max_cost_usd=max_cost_usd, latency_target_ms=latency_target_ms,
    )


MESSAGES = [{"role": "user", "content": "hello"}]

HARD_POLICY = GatewayRoutingPolicy(
    assumed_output_tokens=512,
    balanced_price_tolerance=0.8,
    budget_mode="hard",
    latency_mode="hard",
    high_quality_strategy="lowest_cost",
)

SOFT_POLICY = GatewayRoutingPolicy(
    assumed_output_tokens=512,
    balanced_price_tolerance=0.8,
    budget_mode="soft",
    latency_mode="soft",
    high_quality_strategy="prefer_real",
)


class ParameterizedRouterPolicyTests(unittest.TestCase):
    def setUp(self):
        self.providers = [
            {"name": "real-x", "kind": "real", "privacy": "external",
             "price_per_1k_input": 0.01, "price_per_1k_output": 0.01,
             "typical_latency_ms": 2000,
             "capabilities": {}},
            {"name": "mock-x", "kind": "mock", "privacy": "internal",
             "price_per_1k_input": 0.0005, "price_per_1k_output": 0.0005,
             "typical_latency_ms": 300,
             "capabilities": {}},
        ]

    def test_cost_estimation_uses_policy_assumed_output_tokens(self):
        # With 512 assumed output tokens (policy) vs default 256, the estimated
        # cost for the pricier provider should differ from the P0 default.
        chosen, reason, candidates = select_provider(
            self.providers, MESSAGES, _constraints(quality="cheap"), policy=HARD_POLICY,
        )
        real_x_cost = next(c["est_cost"] for c in candidates if c["name"] == "real-x")
        # input tokens ~ 2 (from "hello"), output 512 * 0.01/1000 = 0.00512 dominates
        self.assertGreater(real_x_cost, 0.005)

    def test_hard_budget_raises_when_none_affordable(self):
        tiny_budget = _constraints(quality="cheap", max_cost_usd=0.0000001)
        with self.assertRaises(RuntimeError):
            select_provider(self.providers, MESSAGES, tiny_budget, policy=HARD_POLICY)

    def test_soft_budget_falls_back_to_cheapest(self):
        tiny_budget = _constraints(quality="cheap", max_cost_usd=0.0000001)
        chosen, reason, _ = select_provider(self.providers, MESSAGES, tiny_budget, policy=SOFT_POLICY)
        self.assertEqual(chosen["name"], "mock-x")

    def test_hard_latency_raises_when_none_meet_target(self):
        strict_latency = _constraints(quality="cheap", latency_target_ms=1)
        with self.assertRaises(RuntimeError):
            select_provider(self.providers, MESSAGES, strict_latency, policy=HARD_POLICY)

    def test_soft_latency_relaxes(self):
        strict_latency = _constraints(quality="cheap", latency_target_ms=1)
        chosen, reason, _ = select_provider(self.providers, MESSAGES, strict_latency, policy=SOFT_POLICY)
        self.assertIsNotNone(chosen)

    def test_prefer_real_and_lowest_cost_choose_different_providers(self):
        c = _constraints(quality="high")
        prefer_real_policy = GatewayRoutingPolicy(
            assumed_output_tokens=256, balanced_price_tolerance=0.2,
            budget_mode="soft", latency_mode="soft", high_quality_strategy="prefer_real",
        )
        lowest_cost_policy = GatewayRoutingPolicy(
            assumed_output_tokens=256, balanced_price_tolerance=0.2,
            budget_mode="soft", latency_mode="soft", high_quality_strategy="lowest_cost",
        )
        chosen_a, _, _ = select_provider(self.providers, MESSAGES, c, policy=prefer_real_policy)
        chosen_b, _, _ = select_provider(self.providers, MESSAGES, c, policy=lowest_cost_policy)
        self.assertEqual(chosen_a["name"], "real-x")
        self.assertEqual(chosen_b["name"], "mock-x")
        self.assertNotEqual(chosen_a["name"], chosen_b["name"])

    def test_privacy_guardrail_unaffected_by_policy(self):
        c = _constraints(quality="high", privacy="high")
        chosen, reason, _ = select_provider(self.providers, MESSAGES, c, policy=HARD_POLICY)
        self.assertEqual(chosen["name"], "mock-x")


if __name__ == "__main__":
    unittest.main(verbosity=2)