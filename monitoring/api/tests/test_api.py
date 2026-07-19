import unittest

from fastapi.testclient import TestClient

from app.main import app, get_prometheus
from app.prometheus import PrometheusError, Sample


def sample(value: float, **labels: str) -> Sample:
    return Sample(labels=labels, value=value)


class FakePrometheus:
    def __init__(
        self,
        *,
        reachable: bool = True,
        fail_queries: bool = False,
        gateway_up: float | None = 1.0,
        no_traffic: bool = False,
    ):
        self.reachable = reachable
        self.fail_queries = fail_queries
        self.gateway_up = gateway_up
        self.no_traffic = no_traffic

    def ready(self) -> bool:
        return self.reachable

    def query_many(self, expressions):
        if self.fail_queries:
            raise PrometheusError("forced query failure")
        if self.no_traffic:
            return {
                "gateway_up": (
                    [sample(self.gateway_up)]
                    if self.gateway_up is not None
                    else []
                ),
                "requests_total": [],
                "request_rate": [],
                "error_rate": [],
                "request_p95": [],
                "cache_total": [],
                "cache_hit_rate": [],
                "input_tokens": [],
                "output_tokens": [],
                "estimated_cost": [],
                "provider_requests": [],
                "provider_success_rate": [],
                "provider_p95": [],
            }
        return {
            "gateway_up": (
                [sample(self.gateway_up)]
                if self.gateway_up is not None
                else []
            ),
            "requests_total": [sample(42)],
            "request_rate": [sample(0.5)],
            "error_rate": [sample(0.1)],
            "request_p95": [sample(420)],
            "cache_total": [sample(42)],
            "cache_hit_rate": [sample(0.25)],
            "input_tokens": [sample(1200)],
            "output_tokens": [sample(500)],
            "estimated_cost": [sample(0.0042)],
            "provider_requests": [
                sample(30, provider="mock-a"),
                sample(12, provider="mock-b"),
            ],
            "provider_success_rate": [
                sample(0.9, provider="mock-a"),
                sample(1.0, provider="mock-b"),
            ],
            "provider_p95": [
                sample(310, provider="mock-a"),
                sample(float("nan"), provider="mock-b"),
            ],
        }


class MonitoringApiTest(unittest.TestCase):
    def tearDown(self):
        app.dependency_overrides.clear()

    def test_health_reports_reachable_prometheus(self):
        app.dependency_overrides[get_prometheus] = lambda: FakePrometheus()

        with TestClient(app) as client:
            response = client.get("/health")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json(),
            {"status": "ok", "prometheus_reachable": True},
        )

    def test_health_is_degraded_when_prometheus_is_unreachable(self):
        app.dependency_overrides[get_prometheus] = lambda: FakePrometheus(
            reachable=False
        )

        with TestClient(app) as client:
            response = client.get("/health")

        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json()["status"], "degraded")
        self.assertFalse(response.json()["prometheus_reachable"])

    def test_overview_maps_prometheus_results_to_stable_json(self):
        app.dependency_overrides[get_prometheus] = lambda: FakePrometheus()

        with TestClient(app) as client:
            response = client.get(
                "/api/monitoring/overview",
                params={"window": "15m"},
            )

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["window"], "15m")
        self.assertEqual(body["gateway"]["requests_total"], 42)
        self.assertEqual(body["gateway"]["p95_latency_ms"], 420)
        self.assertEqual(body["cache"]["hit_rate"], 0.25)
        self.assertEqual(body["usage"]["input_tokens"], 1200)
        self.assertEqual(
            [provider["name"] for provider in body["providers"]],
            ["mock-a", "mock-b"],
        )
        self.assertIsNone(body["providers"][1]["p95_latency_ms"])
        self.assertFalse(body["resources"]["available"])
        self.assertFalse(body["partial"])
        self.assertEqual(body["warnings"], [])

    def test_overview_uses_null_for_no_traffic_ratios(self):
        app.dependency_overrides[get_prometheus] = lambda: FakePrometheus(
            no_traffic=True
        )

        with TestClient(app) as client:
            response = client.get(
                "/api/monitoring/overview",
                params={"window": "5m"},
            )

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["gateway"]["requests_total"], 0)
        self.assertIsNone(body["gateway"]["error_rate"])
        self.assertIsNone(body["gateway"]["p95_latency_ms"])
        self.assertIsNone(body["cache"]["hit_rate"])
        self.assertEqual(body["providers"], [])
        self.assertFalse(body["partial"])
        self.assertGreaterEqual(len(body["warnings"]), 2)

    def test_overview_is_partial_when_gateway_target_is_down(self):
        app.dependency_overrides[get_prometheus] = lambda: FakePrometheus(
            gateway_up=0.0
        )

        with TestClient(app) as client:
            response = client.get("/api/monitoring/overview")

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertTrue(body["partial"])
        self.assertTrue(
            any("DOWN" in warning for warning in body["warnings"])
        )

    def test_overview_is_partial_when_gateway_target_is_missing(self):
        app.dependency_overrides[get_prometheus] = lambda: FakePrometheus(
            gateway_up=None,
            no_traffic=True,
        )

        with TestClient(app) as client:
            response = client.get("/api/monitoring/overview")

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertTrue(body["partial"])
        self.assertTrue(
            any("missing" in warning for warning in body["warnings"])
        )

    def test_overview_rejects_unknown_window(self):
        app.dependency_overrides[get_prometheus] = lambda: FakePrometheus()

        with TestClient(app) as client:
            response = client.get(
                "/api/monitoring/overview",
                params={"window": "2m"},
            )

        self.assertEqual(response.status_code, 422)

    def test_overview_returns_bad_gateway_when_query_fails(self):
        app.dependency_overrides[get_prometheus] = lambda: FakePrometheus(
            fail_queries=True
        )

        with TestClient(app) as client:
            response = client.get("/api/monitoring/overview")

        self.assertEqual(response.status_code, 502)
        self.assertIn("forced query failure", response.json()["detail"])

    def test_local_frontend_origin_is_allowed(self):
        with TestClient(app) as client:
            response = client.options(
                "/api/monitoring/overview",
                headers={
                    "Origin": "http://localhost:8080",
                    "Access-Control-Request-Method": "GET",
                },
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.headers["access-control-allow-origin"],
            "http://localhost:8080",
        )


if __name__ == "__main__":
    unittest.main()
