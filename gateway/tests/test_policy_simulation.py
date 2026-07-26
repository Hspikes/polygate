"""
Task 6, Step 3: failing tests for POST /internal/routing/simulate.
The endpoint does not exist yet (Step 4 implements it) — expected to fail
with 404 right now.
"""
import os
from pathlib import Path
import unittest
from unittest.mock import patch

PROJECT_ROOT = Path(__file__).resolve().parents[2]
os.environ["PROVIDERS_FILE"] = str(PROJECT_ROOT / "contracts" / "providers.yaml")
os.environ["FAKE_ADAPTER"] = "1"
os.environ["REDIS_URL"] = "redis://127.0.0.1:1/0"

from fastapi.testclient import TestClient

from app.main import CACHE, app


client = TestClient(app)


SIMULATION_REQUEST_BODY = {
    "request": {
        "model": "auto",
        "messages": [{"role": "user", "content": "policy simulation"}],
        "polygate": {
            "quality": "high",
            "privacy": "standard",
            "max_cost_usd": 0.01,
            "latency_target_ms": 3000,
        },
    },
    "gateway_policy": {
        "assumed_output_tokens": 256,
        "balanced_price_tolerance": 0.2,
        "budget_mode": "soft",
        "latency_mode": "soft",
        "high_quality_strategy": "lowest_cost",
    },
}


class RoutingSimulationTests(unittest.TestCase):
    def test_simulation_returns_provider_reason_cost_latency(self):
        with patch("app.main.call_provider") as provider_call:
            response = client.post("/internal/routing/simulate", json=SIMULATION_REQUEST_BODY)
        self.assertEqual(response.status_code, 200)
        body = response.json()
        for key in ("provider", "reason", "estimated_cost_usd", "typical_latency_ms"):
            self.assertIn(key, body)
        provider_call.assert_not_called()

    def test_simulation_never_touches_cache(self):
        with (
            patch.object(CACHE, "get") as cache_get,
            patch.object(CACHE, "set") as cache_set,
        ):
            response = client.post("/internal/routing/simulate", json=SIMULATION_REQUEST_BODY)
        self.assertEqual(response.status_code, 200)
        cache_get.assert_not_called()
        cache_set.assert_not_called()

    def test_simulation_applies_privacy_guardrail(self):
        body = {
            **SIMULATION_REQUEST_BODY,
            "request": {
                **SIMULATION_REQUEST_BODY["request"],
                "polygate": {
                    **SIMULATION_REQUEST_BODY["request"]["polygate"],
                    "privacy": "high",
                },
            },
        }
        response = client.post("/internal/routing/simulate", json=body)
        self.assertEqual(response.status_code, 200)
        self.assertIn("provider", response.json())

    def test_policy_editor_preview_routes_high_quality_to_deepseek_pro(self):
        body = {
            **SIMULATION_REQUEST_BODY,
            "gateway_policy": {
                **SIMULATION_REQUEST_BODY["gateway_policy"],
                "high_quality_strategy": "prefer_real",
            },
        }

        response = client.post("/internal/routing/simulate", json=body)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["provider"], "deepseek-pro")
        self.assertIn("quality_rank=2", response.json()["reason"])

    def test_simulation_endpoint_is_absent_from_openapi_schema(self):
        schema = client.get("/openapi.json").json()
        self.assertNotIn("/internal/routing/simulate", schema.get("paths", {}))


if __name__ == "__main__":
    unittest.main(verbosity=2)
