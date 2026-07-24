"""Request-budget, retry and traceability regression tests."""
import asyncio
from dataclasses import replace
import os
from pathlib import Path
import unittest
from unittest.mock import patch

import httpx
from prometheus_client.parser import text_string_to_metric_families


PROJECT_ROOT = Path(__file__).resolve().parents[2]
os.environ["PROVIDERS_FILE"] = str(PROJECT_ROOT / "contracts" / "providers.yaml")
os.environ["FAKE_ADAPTER"] = "1"

from fastapi.testclient import TestClient  # noqa: E402

from app.adapters import AdapterResult  # noqa: E402
from app.circuit_breaker import CircuitBreakerRegistry  # noqa: E402
from app.main import CACHE, RELIABILITY, app  # noqa: E402
from app.resilience import ResilienceSettings  # noqa: E402
from app.retry import (  # noqa: E402
    RetryBudgetExceededError,
    call_provider_with_resilience,
    open_provider_stream_with_resilience,
)


client = TestClient(app)


def metric_total(name: str, labels: dict[str, str]) -> float:
    response = client.get("/metrics")
    response.raise_for_status()
    return sum(
        float(sample.value)
        for family in text_string_to_metric_families(response.text)
        for sample in family.samples
        if sample.name == name
        and all(sample.labels.get(key) == value for key, value in labels.items())
    )


def _status_error(status: int, **headers: str) -> httpx.HTTPStatusError:
    request = httpx.Request("POST", "https://provider.example/v1/chat/completions")
    response = httpx.Response(status, request=request, headers=headers)
    return httpx.HTTPStatusError(
        f"upstream returned {status}", request=request, response=response
    )


class RetryPolicyTests(unittest.TestCase):
    def test_retry_after_is_honored_before_retrying_429(self):
        provider = {"name": "rate-limited"}
        result = AdapterResult(
            {"choices": [{"message": {"content": "ok"}}], "usage": {}}, 1
        )
        attempts = [_status_error(429, **{"Retry-After": "2"}), result]
        delays: list[float] = []

        def provider_call(*_args, **_kwargs):
            value = attempts.pop(0)
            if isinstance(value, Exception):
                raise value
            return value

        retries_before = metric_total(
            "polygate_provider_retries_total",
            {"provider": "rate-limited", "reason": "429"},
        )
        with patch("app.retry.random.uniform", return_value=0):
            returned = call_provider_with_resilience(
                provider,
                {},
                CircuitBreakerRegistry(),
                provider_call=provider_call,
                sleeper=delays.append,
            )

        self.assertIs(returned, result)
        self.assertEqual(delays, [2.0])
        self.assertEqual(returned.retries, 1)
        self.assertEqual(
            metric_total(
                "polygate_provider_retries_total",
                {"provider": "rate-limited", "reason": "429"},
            ),
            retries_before + 1,
        )

    def test_provider_attempt_timeout_is_capped_by_remaining_budget(self):
        provider = {"name": "slow"}
        observed_timeouts: list[float] = []

        def provider_call(*_args, timeout_s: float, **_kwargs):
            observed_timeouts.append(timeout_s)
            return AdapterResult(
                {"choices": [{"message": {"content": "ok"}}], "usage": {}},
                1,
            )

        call_provider_with_resilience(
            provider,
            {},
            CircuitBreakerRegistry(),
            timeout_s=10.0,
            deadline=1.5,
            clock=lambda: 1.0,
            provider_call=provider_call,
        )

        self.assertEqual(observed_timeouts, [0.5])

    def test_retry_is_abandoned_when_delay_exceeds_request_budget(self):
        provider = {"name": "rate-limited"}

        def provider_call(*_args, **_kwargs):
            raise _status_error(429, **{"Retry-After": "10"})

        with self.assertRaises(RetryBudgetExceededError):
            call_provider_with_resilience(
                provider,
                {},
                CircuitBreakerRegistry(),
                deadline=1.0,
                clock=lambda: 0.0,
                provider_call=provider_call,
            )


class StreamBudgetTests(unittest.IsolatedAsyncioTestCase):
    async def test_stream_open_is_cancelled_at_the_total_start_budget(self):
        provider = {"name": "slow-stream"}
        breaker = CircuitBreakerRegistry(failure_threshold=1)

        async def slow_open(*_args, **_kwargs):
            await asyncio.sleep(1)

        with patch("app.retry.open_provider_stream", side_effect=slow_open):
            with self.assertRaises(RetryBudgetExceededError):
                await open_provider_stream_with_resilience(
                    provider,
                    {},
                    breaker,
                    max_retries=0,
                    deadline=0.01,
                    clock=lambda: 0.0,
                )

        self.assertEqual(breaker.health_snapshot()["slow-stream"], "down")


class GatewayReliabilityTests(unittest.TestCase):
    def test_every_chat_response_has_a_request_id(self):
        invalid = client.post("/v1/chat/completions", json={"model": "auto"})
        success = client.post(
            "/v1/chat/completions",
            json={"messages": [{"role": "user", "content": "trace me"}]},
        )
        with patch.dict(os.environ, {"POLYGATE_API_KEYS": "secret"}):
            unauthorized = client.post(
                "/v1/chat/completions",
                json={"messages": [{"role": "user", "content": "no key"}]},
            )

        self.assertEqual(invalid.status_code, 422)
        self.assertEqual(success.status_code, 200)
        self.assertEqual(unauthorized.status_code, 401)
        invalid_id = invalid.headers["x-polygate-request-id"]
        success_id = success.headers["x-polygate-request-id"]
        self.assertTrue(invalid_id.startswith("req_"))
        self.assertTrue(success_id.startswith("req_"))
        self.assertTrue(
            unauthorized.headers["x-polygate-request-id"].startswith("req_")
        )
        self.assertNotEqual(invalid_id, success_id)

    def test_forced_provider_failure_does_not_silently_fail_over(self):
        with (
            patch("app.main.BREAKER", CircuitBreakerRegistry()),
            patch("app.main.call_provider", side_effect=RuntimeError("failed")) as call,
            patch.object(CACHE, "get", return_value=None),
        ):
            response = client.post(
                "/v1/chat/completions",
                json={
                    "model": "mock-a",
                    "messages": [{"role": "user", "content": "stay on mock-a"}],
                },
            )

        self.assertEqual(response.status_code, 502)
        self.assertEqual(call.call_count, 1)
        self.assertTrue(response.headers["x-polygate-request-id"].startswith("req_"))

    def test_provider_timeout_returns_504_without_claiming_budget_exhaustion(self):
        settings = replace(RELIABILITY, max_retries=0)
        exhausted_before = metric_total(
            "polygate_request_budget_exhausted_total",
            {"phase": "non_stream"},
        )
        timeout = httpx.ReadTimeout(
            "provider timed out",
            request=httpx.Request("POST", "https://provider.example/v1/chat"),
        )
        with (
            patch("app.main.RELIABILITY", settings),
            patch("app.main.BREAKER", CircuitBreakerRegistry()),
            patch("app.main.call_provider", side_effect=timeout) as call,
            patch.object(CACHE, "get", return_value=None),
        ):
            response = client.post(
                "/v1/chat/completions",
                json={
                    "model": "mock-a",
                    "messages": [{"role": "user", "content": "time out"}],
                },
            )

        self.assertEqual(response.status_code, 504)
        self.assertEqual(call.call_count, 1)
        self.assertEqual(response.json()["error"]["code"], "provider_timeout")
        self.assertEqual(
            metric_total(
                "polygate_request_budget_exhausted_total",
                {"phase": "non_stream"},
            ),
            exhausted_before,
        )

    def test_expired_non_stream_budget_returns_504_without_failover(self):
        settings = replace(RELIABILITY, non_stream_budget_seconds=1.0)
        exhausted_before = metric_total(
            "polygate_request_budget_exhausted_total",
            {"phase": "non_stream"},
        )
        with (
            patch("app.main.RELIABILITY", settings),
            patch("app.main.BREAKER", CircuitBreakerRegistry()),
            patch("app.main._new_deadline", return_value=float("inf")),
            patch(
                "app.main.call_provider_with_resilience",
                side_effect=RetryBudgetExceededError("mock-b"),
            ) as resilient_call,
            patch.object(CACHE, "get", return_value=None),
        ):
            response = client.post(
                "/v1/chat/completions",
                json={
                    "model": "auto",
                    "messages": [{"role": "user", "content": "bounded turn"}],
                },
            )

        self.assertEqual(response.status_code, 504)
        self.assertEqual(resilient_call.call_count, 1)
        self.assertEqual(response.json()["error"]["code"], "provider_timeout")
        self.assertTrue(response.headers["x-polygate-request-id"].startswith("req_"))
        self.assertEqual(
            metric_total(
                "polygate_request_budget_exhausted_total",
                {"phase": "non_stream"},
            ),
            exhausted_before + 1,
        )

    def test_expired_stream_start_budget_returns_504_without_failover(self):
        settings = replace(RELIABILITY, stream_start_budget_seconds=1.0)
        exhausted_before = metric_total(
            "polygate_request_budget_exhausted_total",
            {"phase": "stream_start"},
        )
        with (
            patch("app.main.RELIABILITY", settings),
            patch("app.main.BREAKER", CircuitBreakerRegistry()),
            patch("app.main._new_deadline", return_value=float("inf")),
            patch(
                "app.main.open_provider_stream_with_resilience",
                side_effect=RetryBudgetExceededError("mock-b"),
            ) as resilient_open,
        ):
            response = client.post(
                "/v1/chat/completions",
                json={
                    "model": "auto",
                    "stream": True,
                    "messages": [
                        {"role": "user", "content": "bounded stream start"}
                    ],
                },
            )

        self.assertEqual(response.status_code, 504)
        self.assertEqual(resilient_open.call_count, 1)
        self.assertEqual(response.json()["error"]["code"], "provider_timeout")
        self.assertTrue(response.headers["x-polygate-request-id"].startswith("req_"))
        self.assertEqual(
            metric_total(
                "polygate_request_budget_exhausted_total",
                {"phase": "stream_start"},
            ),
            exhausted_before + 1,
        )

    def test_auto_failover_is_counted(self):
        failovers_before = metric_total("polygate_failovers_total", {})
        result = AdapterResult(
            {"choices": [{"message": {"content": "ok"}}], "usage": {}}, 1
        )
        with (
            patch("app.main.BREAKER", CircuitBreakerRegistry()),
            patch(
                "app.main.call_provider",
                side_effect=[RuntimeError("first failed"), result],
            ),
            patch.object(CACHE, "get", return_value=None),
        ):
            response = client.post(
                "/v1/chat/completions",
                json={
                    "model": "auto",
                    "messages": [{"role": "user", "content": "fail over"}],
                },
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            metric_total("polygate_failovers_total", {}),
            failovers_before + 1,
        )

    def test_auto_provider_timeout_fails_over_within_request_budget(self):
        settings = replace(RELIABILITY, max_retries=0)
        failovers_before = metric_total("polygate_failovers_total", {})
        exhausted_before = metric_total(
            "polygate_request_budget_exhausted_total",
            {"phase": "non_stream"},
        )
        timeout = httpx.ReadTimeout(
            "provider timed out",
            request=httpx.Request("POST", "https://provider.example/v1/chat"),
        )
        result = AdapterResult(
            {"choices": [{"message": {"content": "fallback ok"}}], "usage": {}},
            1,
        )
        with (
            patch("app.main.RELIABILITY", settings),
            patch("app.main.BREAKER", CircuitBreakerRegistry()),
            patch("app.main.call_provider", side_effect=[timeout, result]) as call,
            patch.object(CACHE, "get", return_value=None),
        ):
            response = client.post(
                "/v1/chat/completions",
                json={
                    "model": "auto",
                    "messages": [{"role": "user", "content": "fail over on timeout"}],
                },
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(call.call_count, 2)
        self.assertEqual(
            metric_total("polygate_failovers_total", {}),
            failovers_before + 1,
        )
        self.assertEqual(
            metric_total(
                "polygate_request_budget_exhausted_total",
                {"phase": "non_stream"},
            ),
            exhausted_before,
        )


class ResilienceSettingsTests(unittest.TestCase):
    def test_environment_overrides_are_validated(self):
        with patch.dict(
            os.environ,
            {
                "PROVIDER_MAX_RETRIES": "4",
                "GATEWAY_STREAM_START_BUDGET_SECONDS": "12.5",
            },
        ):
            settings = ResilienceSettings.from_env()

        self.assertEqual(settings.max_retries, 4)
        self.assertEqual(settings.stream_start_budget_seconds, 12.5)

        with patch.dict(os.environ, {"PROVIDER_MAX_RETRIES": "100"}):
            with self.assertRaisesRegex(ValueError, "between 0 and 10"):
                ResilienceSettings.from_env()

        with patch.dict(os.environ, {"PROVIDER_TIMEOUT_SECONDS": "nan"}):
            with self.assertRaisesRegex(ValueError, "finite"):
                ResilienceSettings.from_env()


if __name__ == "__main__":
    unittest.main()
