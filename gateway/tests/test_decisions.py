"""Decision Record persistence and retrieval contract tests."""
from dataclasses import replace
from datetime import UTC, datetime, timedelta
import os
from pathlib import Path
import unittest
from unittest.mock import AsyncMock, patch

import httpx

PROJECT_ROOT = Path(__file__).resolve().parents[2]
os.environ["PROVIDERS_FILE"] = str(PROJECT_ROOT / "contracts" / "providers.yaml")
os.environ["FAKE_ADAPTER"] = "1"

from fastapi.testclient import TestClient  # noqa: E402

from app.adapters import AdapterResult, OpenedProviderStream  # noqa: E402
from app.decisions import (  # noqa: E402
    DecisionRecord,
    DecisionStore,
    DecisionStoreUnavailable,
)
from app.main import CACHE, RELIABILITY, app  # noqa: E402
from app.models import Tokens  # noqa: E402


client = TestClient(app)


class MemoryCache:
    def __init__(self, *, enabled: bool = True):
        self.available = enabled
        self.values: dict[str, dict] = {}
        self.ttls: dict[str, int] = {}

    @property
    def enabled(self) -> bool:
        return self.available

    def get(self, key: str):
        return self.values.get(key)

    def set(self, key: str, value: dict, ttl: int = 3600):
        self.values[key] = value
        self.ttls[key] = ttl


def sample_record(request_id: str = "req_" + "a" * 32) -> DecisionRecord:
    now = datetime.now(UTC)
    return DecisionRecord(
        request_id=request_id,
        outcome="success",
        chosen_provider="mock-a",
        initial_provider="mock-b",
        reason="mock-b failed; selected mock-a",
        cache_hit=False,
        stream=False,
        cost_estimate_usd=0.0001,
        latency_ms=123,
        tokens=Tokens(input=3, output=4),
        retries=2,
        failover_from="mock-b",
        failover_count=1,
        created_at=now.isoformat(),
        expires_at=(now + timedelta(seconds=300)).isoformat(),
    )


class DecisionStoreTests(unittest.TestCase):
    def test_store_uses_short_ttl_and_only_serializes_public_contract_fields(self):
        cache = MemoryCache()
        store = DecisionStore(cache, ttl_seconds=300)
        record = sample_record()

        self.assertTrue(store.save(record))
        stored = cache.values[f"pg:decision:{record.request_id}"]

        self.assertEqual(cache.ttls[f"pg:decision:{record.request_id}"], 300)
        self.assertEqual(
            set(stored),
            {
                "schema_version",
                "request_id",
                "outcome",
                "chosen_provider",
                "initial_provider",
                "reason",
                "cache_hit",
                "stream",
                "cost_estimate_usd",
                "latency_ms",
                "tokens",
                "retries",
                "failover_from",
                "failover_count",
                "created_at",
                "expires_at",
            },
        )
        serialized = str(stored).lower()
        self.assertNotIn("prompt", serialized)
        self.assertNotIn("authorization", serialized)
        self.assertNotIn("endpoint", serialized)
        self.assertEqual(store.get(record.request_id), record)

    def test_unavailable_redis_is_distinct_from_an_expired_record(self):
        missing = DecisionStore(MemoryCache(), ttl_seconds=300)
        self.assertIsNone(missing.get("req_" + "b" * 32))

        unavailable = DecisionStore(MemoryCache(enabled=False), ttl_seconds=300)
        with self.assertRaises(DecisionStoreUnavailable):
            unavailable.get("req_" + "b" * 32)

    def test_ttl_environment_value_is_bounded(self):
        with patch.dict(os.environ, {"DECISION_RECORD_TTL_SECONDS": "900"}):
            self.assertEqual(DecisionStore(MemoryCache()).ttl_seconds, 900)

        for invalid in ("59", "86401", "not-an-integer"):
            with self.subTest(invalid=invalid):
                with patch.dict(
                    os.environ,
                    {"DECISION_RECORD_TTL_SECONDS": invalid},
                ):
                    with self.assertRaises(ValueError):
                        DecisionStore(MemoryCache())


class DecisionApiTests(unittest.TestCase):
    def test_non_stream_completion_writes_authenticated_decision_record(self):
        store = DecisionStore(MemoryCache(), ttl_seconds=300)
        with (
            patch("app.main.DECISIONS", store),
            patch.object(CACHE, "get", return_value=None),
            patch.dict(os.environ, {"POLYGATE_API_KEYS": "web-secret"}),
        ):
            response = client.post(
                "/v1/chat/completions",
                headers={"Authorization": "Bearer web-secret"},
                json={
                    "model": "auto",
                    "messages": [
                        {"role": "user", "content": "private prompt must not persist"}
                    ],
                },
            )
            request_id = response.headers["x-polygate-request-id"]
            unauthenticated = client.get(f"/v1/decisions/{request_id}")
            decision = client.get(
                f"/v1/decisions/{request_id}",
                headers={"Authorization": "Bearer web-secret"},
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(unauthenticated.status_code, 401)
        self.assertEqual(decision.status_code, 200)
        payload = decision.json()
        self.assertEqual(payload["request_id"], request_id)
        self.assertEqual(payload["outcome"], "success")
        self.assertFalse(payload["stream"])
        self.assertNotIn("private prompt", str(payload))

    def test_decision_record_accumulates_retries_before_failover(self):
        store = DecisionStore(MemoryCache(), ttl_seconds=300)
        errors = [
            httpx.ReadError(
                "connection reset",
                request=httpx.Request("POST", "https://provider.example/v1/chat"),
            )
            for _ in range(5)
        ]
        result = AdapterResult(
            {
                "choices": [{"message": {"content": "fallback ok"}}],
                "usage": {"prompt_tokens": 2, "completion_tokens": 1},
            },
            1,
        )
        settings = replace(RELIABILITY, retry_base_delay_seconds=0)
        with (
            patch("app.main.DECISIONS", store),
            patch("app.main.RELIABILITY", settings),
            patch("app.main.call_provider", side_effect=[*errors, result]),
            patch.object(CACHE, "get", return_value=None),
        ):
            response = client.post(
                "/v1/chat/completions",
                json={
                    "model": "auto",
                    "messages": [{"role": "user", "content": "retry then fail over"}],
                },
            )
            request_id = response.headers["x-polygate-request-id"]
            decision = client.get(f"/v1/decisions/{request_id}")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(decision.status_code, 200)
        self.assertEqual(decision.json()["retries"], 4)
        self.assertEqual(decision.json()["failover_count"], 1)
        self.assertEqual(
            decision.json()["failover_from"],
            decision.json()["initial_provider"],
        )

    def test_stream_completion_persists_final_usage_after_done(self):
        store = DecisionStore(MemoryCache(), ttl_seconds=300)
        with patch("app.main.DECISIONS", store):
            response = client.post(
                "/v1/chat/completions",
                json={
                    "model": "auto",
                    "stream": True,
                    "stream_options": {"include_usage": True},
                    "messages": [{"role": "user", "content": "stream me"}],
                },
            )
            request_id = response.headers["x-polygate-request-id"]
            decision = client.get(f"/v1/decisions/{request_id}")

        self.assertEqual(response.status_code, 200)
        self.assertIn("data: [DONE]", response.text)
        self.assertEqual(decision.status_code, 200)
        self.assertEqual(decision.json()["outcome"], "success")
        self.assertTrue(decision.json()["stream"])
        self.assertGreater(decision.json()["tokens"]["output"], 0)

    def test_truncated_stream_persists_partial_error_instead_of_success(self):
        async def truncated_remaining():
            if False:
                yield b""

        opened = OpenedProviderStream(
            response=None,
            first_event=(
                b'data: {"choices":[{"delta":{"content":"partial"},'
                b'"finish_reason":null}]}\n\n'
            ),
            remaining_events=truncated_remaining(),
            latency_to_first_event_ms=1,
        )
        store = DecisionStore(MemoryCache(), ttl_seconds=300)
        with (
            patch("app.main.DECISIONS", store),
            patch(
                "app.main.open_provider_stream_with_resilience",
                AsyncMock(return_value=opened),
            ),
        ):
            response = client.post(
                "/v1/chat/completions",
                json={
                    "model": "auto",
                    "stream": True,
                    "messages": [{"role": "user", "content": "truncate me"}],
                },
            )
            request_id = response.headers["x-polygate-request-id"]
            decision = client.get(f"/v1/decisions/{request_id}")

        self.assertEqual(response.status_code, 200)
        self.assertIn("partial", response.text)
        self.assertNotIn("data: [DONE]", response.text)
        self.assertEqual(decision.status_code, 200)
        self.assertEqual(decision.json()["outcome"], "partial_error")

    def test_missing_invalid_and_unavailable_records_have_stable_statuses(self):
        missing_store = DecisionStore(MemoryCache(), ttl_seconds=300)
        with patch("app.main.DECISIONS", missing_store):
            missing = client.get("/v1/decisions/req_" + "c" * 32)
            invalid = client.get("/v1/decisions/not-a-request-id")

        unavailable_store = DecisionStore(
            MemoryCache(enabled=False),
            ttl_seconds=300,
        )
        with patch("app.main.DECISIONS", unavailable_store):
            unavailable = client.get("/v1/decisions/req_" + "d" * 32)

        self.assertEqual(missing.status_code, 404)
        self.assertEqual(missing.json()["detail"], "decision record not found")
        self.assertEqual(invalid.status_code, 422)
        self.assertEqual(unavailable.status_code, 503)
        self.assertEqual(
            unavailable.json()["detail"],
            "decision record store unavailable",
        )


if __name__ == "__main__":
    unittest.main()
