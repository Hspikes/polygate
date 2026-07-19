import os
from pathlib import Path
import unittest
from unittest.mock import patch

from prometheus_client.parser import text_string_to_metric_families


PROJECT_ROOT = Path(__file__).resolve().parents[2]
os.environ["PROVIDERS_FILE"] = str(PROJECT_ROOT / "contracts" / "providers.yaml")
os.environ["FAKE_ADAPTER"] = "1"
os.environ["REDIS_URL"] = "redis://127.0.0.1:1/0"

from fastapi.testclient import TestClient

from app.main import CACHE, app


client = TestClient(app)


def metric_value(name: str, labels: dict[str, str]) -> float:
    response = client.get("/metrics")
    response.raise_for_status()
    for family in text_string_to_metric_families(response.text):
        for sample in family.samples:
            if sample.name == name and all(sample.labels.get(k) == v for k, v in labels.items()):
                return float(sample.value)
    return 0.0


class GatewayMetricsTest(unittest.TestCase):
    def test_metrics_endpoint_uses_prometheus_text_format(self):
        response = client.get("/metrics")

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.headers["content-type"].startswith("text/plain"))
        self.assertIn("# HELP polygate_requests_total", response.text)
        self.assertIn("# HELP polygate_provider_duration_seconds", response.text)

    def test_successful_request_updates_business_metrics(self):
        request_before = metric_value(
            "polygate_requests_total",
            {"outcome": "success"},
        )
        cache_miss_before = metric_value(
            "polygate_cache_requests_total",
            {"result": "miss"},
        )

        response = client.post(
            "/v1/chat/completions",
            json={
                "model": "auto",
                "messages": [{"role": "user", "content": "metrics test request"}],
                "polygate": {
                    "quality": "balanced",
                    "privacy": "standard",
                    "max_cost_usd": 0.01,
                    "latency_target_ms": 3000,
                },
            },
        )

        self.assertEqual(response.status_code, 200)
        provider = response.json()["polygate"]["chosen_provider"]

        self.assertEqual(
            metric_value("polygate_requests_total", {"outcome": "success"}),
            request_before + 1,
        )
        self.assertEqual(
            metric_value("polygate_cache_requests_total", {"result": "miss"}),
            cache_miss_before + 1,
        )
        self.assertGreaterEqual(
            metric_value(
                "polygate_provider_requests_total",
                {"provider": provider, "outcome": "success"},
            ),
            1,
        )
        self.assertGreater(
            metric_value(
                "polygate_provider_duration_seconds_count",
                {"provider": provider, "outcome": "success"},
            ),
            0,
        )
        self.assertGreater(
            metric_value(
                "polygate_tokens_total",
                {"provider": provider, "direction": "input"},
            ),
            0,
        )
        self.assertGreater(
            metric_value(
                "polygate_estimated_cost_usd_total",
                {"provider": provider},
            ),
            0,
        )

    def test_cache_hit_updates_cache_and_request_metrics_without_provider_call(self):
        cache_hit_before = metric_value(
            "polygate_cache_requests_total",
            {"result": "hit"},
        )
        request_before = metric_value(
            "polygate_requests_total",
            {"outcome": "cache_hit"},
        )

        with (
            patch.object(
                CACHE,
                "get",
                return_value={
                    "answer": "cached answer",
                    "tokens": {"input": 10, "output": 5},
                },
            ),
            patch("app.main.call_provider") as provider_call,
        ):
            response = client.post(
                "/v1/chat/completions",
                json={
                    "model": "auto",
                    "messages": [{"role": "user", "content": "cached metrics test"}],
                },
            )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["polygate"]["cache_hit"])
        provider_call.assert_not_called()
        self.assertEqual(
            metric_value("polygate_cache_requests_total", {"result": "hit"}),
            cache_hit_before + 1,
        )
        self.assertEqual(
            metric_value("polygate_requests_total", {"outcome": "cache_hit"}),
            request_before + 1,
        )

    def test_provider_error_updates_provider_and_request_error_metrics(self):
        provider_error_before = metric_value(
            "polygate_provider_requests_total",
            {"provider": "mock-a", "outcome": "error"},
        )
        request_error_before = metric_value(
            "polygate_requests_total",
            {"outcome": "provider_error"},
        )

        with patch("app.main.call_provider", side_effect=RuntimeError("forced failure")):
            response = client.post(
                "/v1/chat/completions",
                json={
                    "model": "mock-a",
                    "messages": [{"role": "user", "content": "provider error metrics test"}],
                },
            )

        self.assertEqual(response.status_code, 502)
        self.assertEqual(
            metric_value(
                "polygate_provider_requests_total",
                {"provider": "mock-a", "outcome": "error"},
            ),
            provider_error_before + 1,
        )
        self.assertEqual(
            metric_value(
                "polygate_requests_total",
                {"outcome": "provider_error"},
            ),
            request_error_before + 1,
        )

    def test_client_errors_are_counted_once_without_cache_lookup(self):
        client_error_before = metric_value(
            "polygate_requests_total",
            {"outcome": "client_error"},
        )
        duration_before = metric_value(
            "polygate_request_duration_seconds_count",
            {"outcome": "client_error"},
        )
        cache_before = (
            metric_value(
                "polygate_cache_requests_total",
                {"result": "hit"},
            )
            + metric_value(
                "polygate_cache_requests_total",
                {"result": "miss"},
            )
        )

        responses = [
            client.post(
                "/v1/chat/completions",
                json={
                    "model": "does-not-exist",
                    "messages": [
                        {
                            "role": "user",
                            "content": "unknown provider metrics test",
                        }
                    ],
                },
            ),
            client.post(
                "/v1/chat/completions",
                json={
                    "model": "real-a",
                    "messages": [
                        {
                            "role": "user",
                            "content": "privacy metrics test",
                        }
                    ],
                    "polygate": {"privacy": "high"},
                },
            ),
            client.post(
                "/v1/chat/completions",
                json={"model": "auto"},
            ),
        ]

        self.assertEqual(
            [response.status_code for response in responses],
            [400, 403, 422],
        )
        self.assertEqual(
            metric_value(
                "polygate_requests_total",
                {"outcome": "client_error"},
            ),
            client_error_before + 3,
        )
        self.assertEqual(
            metric_value(
                "polygate_request_duration_seconds_count",
                {"outcome": "client_error"},
            ),
            duration_before + 3,
        )
        cache_after = (
            metric_value(
                "polygate_cache_requests_total",
                {"result": "hit"},
            )
            + metric_value(
                "polygate_cache_requests_total",
                {"result": "miss"},
            )
        )
        self.assertEqual(cache_after, cache_before)


if __name__ == "__main__":
    unittest.main()
