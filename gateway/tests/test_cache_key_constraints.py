"""Cache keys must cover every constraint that can change routing."""
import unittest

from app.cache import cache_key


class CacheKeyConstraintTests(unittest.TestCase):
    def setUp(self):
        self.messages = [{"role": "user", "content": "same prompt"}]
        self.base = {
            "privacy": "standard",
            "scope": "auto",
            "quality": "balanced",
            "max_cost_usd": 0.01,
            "latency_target_ms": 3000,
            "policy_version": 1,
        }

    def key(self, **overrides):
        values = {**self.base, **overrides}
        return cache_key(self.messages, **values)

    def test_identical_constraints_produce_identical_key(self):
        self.assertEqual(self.key(), self.key())

    def test_every_routing_constraint_changes_key(self):
        variants = [
            self.key(privacy="high"),
            self.key(scope="mock-a"),
            self.key(quality="cheap"),
            self.key(max_cost_usd=0.02),
            self.key(latency_target_ms=500),
            self.key(policy_version=2),
        ]
        self.assertEqual(len(set([self.key(), *variants])), len(variants) + 1)

    def test_same_policy_version_and_request_stays_stable(self):
        self.assertEqual(
            self.key(policy_version=1),
            self.key(policy_version=1),
        )


if __name__ == "__main__":
    unittest.main()