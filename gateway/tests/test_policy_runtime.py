"""
Tests for GatewayPolicyRuntime (Task 5, Step 1 — written before the implementation exists).

Covers:
  - mounted policy v1 loads on startup
  - HTTP v2 replaces v1
  - 304 preserves v1
  - timeout preserves Last Known Good
  - invalid policy preserves Last Known Good
  - refresh interval defaults to 5 seconds

Uses httpx.MockTransport; no live Automation dependency.

Confirmed with C: mounted file path env var is POLICY_FILE (default
/config/policy-store.json, ConfigMap mounted as a directory without
subPath so Kubernetes updates propagate). Policy API base URL env var is
POLICY_API_URL=http://automation:8020, confirmed unchanged.
"""
from __future__ import annotations

import json
import os
import unittest
from pathlib import Path
from unittest.mock import patch

import httpx

from app.policy import GatewayPolicyRuntime, GatewayRoutingPolicy


def _v1_gateway_policy() -> dict:
    return {
        "assumed_output_tokens": 256,
        "balanced_price_tolerance": 0.2,
        "budget_mode": "soft",
        "latency_mode": "soft",
        "high_quality_strategy": "prefer_real",
    }


def _v2_gateway_policy() -> dict:
    return {
        "assumed_output_tokens": 512,
        "balanced_price_tolerance": 0.35,
        "budget_mode": "hard",
        "latency_mode": "hard",
        "high_quality_strategy": "lowest_cost",
    }


def _store_document(active_version: int, gateway_policy: dict) -> dict:
    return {
        "active_version": active_version,
        "versions": [
            {
                "version": active_version,
                "status": "active",
                "created_at": "2026-07-24T00:00:00Z",
                "created_by": "test",
                "change_note": "test fixture",
                "rollback_from": None,
                "policy": {
                    "schema_version": 1,
                    "gateway": gateway_policy,
                    "automation": {
                        "urgency_scores": {"critical": 100, "high": 60, "normal": 30, "low": 10},
                        "scenarios": {
                            "production_incident": {"weight": 40, "defaults": {"quality": "high", "privacy": "high", "max_cost_usd": 0.01, "latency_target_ms": 1000}},
                            "customer_escalation": {"weight": 25, "defaults": {"quality": "balanced", "privacy": "standard", "max_cost_usd": 0.01, "latency_target_ms": 1500}},
                            "finance_summary": {"weight": 15, "defaults": {"quality": "balanced", "privacy": "high", "max_cost_usd": 0.005, "latency_target_ms": 3000}},
                            "marketing_batch": {"weight": 0, "defaults": {"quality": "cheap", "privacy": "standard", "max_cost_usd": 0.002, "latency_target_ms": 5000}},
                        },
                        "queue": {"waiting_bonus_interval_seconds": 5, "waiting_bonus_points": 1, "waiting_bonus_cap": 30, "starvation_streak_threshold": 3, "starvation_wait_seconds": 20},
                    },
                },
            }
        ],
    }


def _active_response_body(version: int, gateway_policy: dict) -> dict:
    # Task 2 ActivePolicyResponse envelope — Gateway must read body["policy"]["gateway"],
    # never a top-level "gateway" field.
    return {
        "version": version,
        "schema_version": 1,
        "published_at": "2026-07-24T10:30:00Z",
        "policy": {
            "schema_version": 1,
            "gateway": gateway_policy,
            "automation": _store_document(version, gateway_policy)["versions"][0]["policy"]["automation"],
        },
    }


class GatewayPolicyRuntimeTests(unittest.TestCase):
    def _write_mounted_store(self, tmp_path: Path, active_version: int, gateway_policy: dict) -> Path:
        store_path = tmp_path / "policy-store.json"
        store_path.write_text(json.dumps(_store_document(active_version, gateway_policy)), encoding="utf-8")
        return store_path

    def test_mounted_policy_v1_loads_on_startup(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            store_path = self._write_mounted_store(Path(tmp), 1, _v1_gateway_policy())
            transport = httpx.MockTransport(lambda request: httpx.Response(304))
            runtime = GatewayPolicyRuntime(
                store_path=store_path,
                api_base_url="http://automation:8020",
                transport=transport,
            )
            snapshot = runtime.snapshot()
            self.assertEqual(snapshot.version, 1)
            self.assertEqual(snapshot.gateway.high_quality_strategy, "prefer_real")

    def test_http_v2_replaces_v1(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            store_path = self._write_mounted_store(Path(tmp), 1, _v1_gateway_policy())

            def handler(request: httpx.Request) -> httpx.Response:
                return httpx.Response(200, json=_active_response_body(2, _v2_gateway_policy()))

            transport = httpx.MockTransport(handler)
            runtime = GatewayPolicyRuntime(
                store_path=store_path,
                api_base_url="http://automation:8020",
                transport=transport,
            )
            runtime.refresh_once()
            snapshot = runtime.snapshot()
            self.assertEqual(snapshot.version, 2)
            self.assertEqual(snapshot.gateway.high_quality_strategy, "lowest_cost")
            self.assertEqual(snapshot.gateway.assumed_output_tokens, 512)

    def test_304_preserves_v1(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            store_path = self._write_mounted_store(Path(tmp), 1, _v1_gateway_policy())
            transport = httpx.MockTransport(lambda request: httpx.Response(304))
            runtime = GatewayPolicyRuntime(
                store_path=store_path,
                api_base_url="http://automation:8020",
                transport=transport,
            )
            before = runtime.snapshot()
            runtime.refresh_once()
            after = runtime.snapshot()
            self.assertEqual(before.version, after.version)
            self.assertEqual(before.gateway, after.gateway)

    def test_timeout_preserves_last_known_good(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            store_path = self._write_mounted_store(Path(tmp), 1, _v1_gateway_policy())

            def handler(request: httpx.Request) -> httpx.Response:
                raise httpx.TimeoutException("simulated timeout", request=request)

            transport = httpx.MockTransport(handler)
            runtime = GatewayPolicyRuntime(
                store_path=store_path,
                api_base_url="http://automation:8020",
                transport=transport,
            )
            before = runtime.snapshot()
            runtime.refresh_once()  # should not raise
            after = runtime.snapshot()
            self.assertEqual(before.version, after.version)

    def test_invalid_policy_preserves_last_known_good(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            store_path = self._write_mounted_store(Path(tmp), 1, _v1_gateway_policy())

            def handler(request: httpx.Request) -> httpx.Response:
                bad = _active_response_body(2, _v2_gateway_policy())
                bad["policy"]["gateway"]["budget_mode"] = "not-a-real-mode"  # invalid enum
                return httpx.Response(200, json=bad)

            transport = httpx.MockTransport(handler)
            runtime = GatewayPolicyRuntime(
                store_path=store_path,
                api_base_url="http://automation:8020",
                transport=transport,
            )
            before = runtime.snapshot()
            runtime.refresh_once()  # should not raise, should reject and keep LKG
            after = runtime.snapshot()
            self.assertEqual(before.version, after.version)
            self.assertEqual(after.gateway.budget_mode, "soft")

    def test_refresh_interval_defaults_to_5_seconds(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            store_path = self._write_mounted_store(Path(tmp), 1, _v1_gateway_policy())
            transport = httpx.MockTransport(lambda request: httpx.Response(304))
            with patch.dict(os.environ, {}, clear=False):
                os.environ.pop("POLICY_REFRESH_SECONDS", None)
                runtime = GatewayPolicyRuntime(
                    store_path=store_path,
                    api_base_url="http://automation:8020",
                    transport=transport,
                )
                self.assertEqual(runtime.refresh_interval_seconds, 5)


if __name__ == "__main__":
    unittest.main(verbosity=2)