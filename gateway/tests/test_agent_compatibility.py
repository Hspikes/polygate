"""Agent request, SSE, cache and authentication regression tests."""
import json
import os
from pathlib import Path
import unittest
from unittest.mock import AsyncMock, patch

import httpx


PROJECT_ROOT = Path(__file__).resolve().parents[2]
os.environ["PROVIDERS_FILE"] = str(PROJECT_ROOT / "contracts" / "providers.yaml")
os.environ["FAKE_ADAPTER"] = "1"

from fastapi.testclient import TestClient  # noqa: E402

from app.adapters import (  # noqa: E402
    AdapterResult,
    OpenedProviderStream,
    ProviderProtocolError,
    ProviderPayloadError,
    _is_completion_event,
    build_provider_payload,
)
from app.circuit_breaker import CircuitBreakerRegistry  # noqa: E402
from app.main import CACHE, app  # noqa: E402
from app.models import GatewayRequest  # noqa: E402
from app.retry import (  # noqa: E402
    call_provider_with_resilience,
    open_provider_stream_with_resilience,
)


client = TestClient(app)


def data_event(payload: dict) -> bytes:
    return f"data: {json.dumps(payload, separators=(',', ':'))}\n\n".encode()


def pi_payload() -> dict:
    return {
        "model": "auto",
        "messages": [
            {"role": "developer", "content": "Use tools when needed."},
            {"role": "user", "content": [{"type": "text", "text": "read the fixture"}]},
        ],
        "tools": [
            {
                "type": "function",
                "function": {
                    "name": "read",
                    "description": "Read a file",
                    "parameters": {
                        "type": "object",
                        "properties": {"path": {"type": "string"}},
                        "required": ["path"],
                    },
                },
            }
        ],
        "tool_choice": "auto",
        "parallel_tool_calls": True,
        "stream": True,
        "stream_options": {"include_usage": True},
        "store": False,
        "temperature": 0.2,
        "max_tokens": 256,
        "polygate": {
            "quality": "balanced",
            "privacy": "standard",
            "max_cost_usd": 0.002,
            "latency_target_ms": 3000,
        },
    }


class AgentCompatibilityTests(unittest.TestCase):
    def test_pi_request_model_preserves_agent_fields(self):
        request = GatewayRequest.model_validate(pi_payload())
        provider_payload = request.provider_payload()

        self.assertEqual(request.required_capabilities(), {"streaming", "tools", "parallel_tool_calls"})
        self.assertTrue(request.bypass_cache())
        self.assertNotIn("polygate", provider_payload)
        self.assertEqual(provider_payload["messages"][0]["role"], "developer")
        self.assertIsInstance(provider_payload["messages"][1]["content"], list)
        self.assertEqual(provider_payload["tools"][0]["function"]["name"], "read")

    def test_store_true_is_a_hard_provider_capability(self):
        request = GatewayRequest.model_validate({
            "messages": [{"role": "user", "content": "remember this"}],
            "store": True,
        })

        self.assertEqual(request.required_capabilities(), {"store"})

    def test_non_boolean_include_usage_is_rejected(self):
        payload = {
            "messages": [{"role": "user", "content": "hello"}],
            "stream": True,
            "stream_options": {"include_usage": "false"},
        }

        response = client.post("/v1/chat/completions", json=payload)

        self.assertEqual(response.status_code, 422)
        self.assertIn("include_usage must be a boolean", str(response.json()))

    def test_deepseek_adapter_flattens_text_blocks_and_maps_developer(self):
        request = GatewayRequest.model_validate(pi_payload())
        provider = {
            "name": "real-a",
            "model": "deepseek-v4-flash",
            "capabilities": {
                "content_blocks": False,
                "developer_role": False,
            },
        }

        payload = build_provider_payload(provider, request.provider_payload())

        self.assertEqual(payload["model"], "deepseek-v4-flash")
        self.assertEqual(payload["messages"][0]["role"], "system")
        self.assertEqual(payload["messages"][1]["content"], "read the fixture")
        self.assertEqual(payload["tools"][0]["function"]["name"], "read")
        self.assertTrue(payload["stream_options"]["include_usage"])
        self.assertNotIn("store", payload)

    def test_stream_adapter_always_requests_usage_for_internal_accounting(self):
        request_payload = pi_payload()
        request_payload["stream_options"] = {"include_usage": False}
        request = GatewayRequest.model_validate(request_payload)
        provider = {
            "name": "mock-a",
            "model": "mock-fast",
            "capabilities": {"content_blocks": True, "store": True},
        }

        payload = build_provider_payload(provider, request.provider_payload())

        self.assertTrue(payload["stream_options"]["include_usage"])

    def test_generation_options_and_message_metadata_bypass_legacy_cache(self):
        simple = GatewayRequest.model_validate({
            "messages": [{"role": "user", "content": "hello"}],
        })
        with_temperature = GatewayRequest.model_validate({
            "messages": [{"role": "user", "content": "hello"}],
            "temperature": 0.2,
        })
        with_name = GatewayRequest.model_validate({
            "messages": [{"role": "user", "content": "hello", "name": "operator"}],
        })
        with_session = GatewayRequest.model_validate({
            "messages": [{"role": "user", "content": "hello"}],
            "polygate": {"session_id": "session-1"},
        })

        self.assertFalse(simple.bypass_cache())
        self.assertTrue(with_temperature.bypass_cache())
        self.assertTrue(with_name.bypass_cache())
        self.assertTrue(with_session.bypass_cache())

    def test_stream_preserves_incremental_tool_call_and_bypasses_cache(self):
        base = {
            "id": "chatcmpl-agent-test",
            "object": "chat.completion.chunk",
            "created": 1,
            "model": "mock-cheap",
        }
        first = data_event({
            **base,
            "choices": [{
                "index": 0,
                "delta": {
                    "role": "assistant",
                    "tool_calls": [{
                        "index": 0,
                        "id": "call_test",
                        "type": "function",
                        "function": {"name": "read", "arguments": "{\"pa"},
                    }],
                },
                "finish_reason": None,
            }],
        })
        remaining_values = [
            data_event({
                **base,
                "choices": [{
                    "index": 0,
                    "delta": {"tool_calls": [{"index": 0, "function": {"arguments": "th\":\"/tmp/x\"}"}}]},
                    "finish_reason": None,
                }],
            }),
            data_event({
                **base,
                "choices": [{"index": 0, "delta": {}, "finish_reason": "tool_calls"}],
            }),
            data_event({
                **base,
                "choices": [],
                "usage": {"prompt_tokens": 10, "completion_tokens": 3, "total_tokens": 13},
            }),
            b"data: [DONE]\n\n",
        ]

        async def remaining():
            for value in remaining_values:
                yield value

        opened = OpenedProviderStream(None, first, remaining(), 3)
        async_open = AsyncMock(return_value=opened)
        with (
            patch("app.main.open_provider_stream_with_resilience", async_open),
            patch.object(CACHE, "get") as cache_get,
        ):
            response = client.post("/v1/chat/completions", json=pi_payload())

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.headers["content-type"].startswith("text/event-stream"))
        self.assertTrue(response.headers["x-polygate-request-id"].startswith("req_"))
        self.assertIn('"finish_reason":"tool_calls"', response.text)
        self.assertIn("data: [DONE]", response.text)
        cache_get.assert_not_called()
        forwarded_payload = async_open.await_args.args[1]
        self.assertNotIn("polygate", forwarded_payload)
        self.assertIn("tools", forwarded_payload)

    def test_configured_api_key_is_required(self):
        payload = {
            "model": "auto",
            "messages": [{"role": "user", "content": "hello"}],
        }
        with patch.dict(os.environ, {"POLYGATE_API_KEYS": "secret-one,secret-two"}):
            missing = client.post("/v1/chat/completions", json=payload)
            wrong = client.post(
                "/v1/chat/completions",
                json=payload,
                headers={"Authorization": "Bearer wrong"},
            )
            accepted = client.post(
                "/v1/chat/completions",
                json=payload,
                headers={"Authorization": "Bearer secret-two"},
            )

        self.assertEqual(missing.status_code, 401)
        self.assertEqual(wrong.status_code, 401)
        self.assertEqual(accepted.status_code, 200)

    def test_failover_result_is_not_written_to_the_exact_cache(self):
        response_body = {
            "id": "chatcmpl-fallback-json",
            "object": "chat.completion",
            "model": "mock-fast",
            "choices": [{
                "index": 0,
                "message": {"role": "assistant", "content": "fallback answer"},
                "finish_reason": "stop",
            }],
            "usage": {"prompt_tokens": 2, "completion_tokens": 2},
        }

        def provider_call(provider, _payload, timeout_s):
            del timeout_s
            if provider["name"] == "mock-b":
                raise RuntimeError("primary failed")
            return AdapterResult(response_body, 1)

        with (
            patch("app.main.BREAKER", CircuitBreakerRegistry()),
            patch("app.main.call_provider", side_effect=provider_call),
            patch.object(CACHE, "get", return_value=None),
            patch.object(CACHE, "set") as cache_set,
        ):
            response = client.post(
                "/v1/chat/completions",
                json={
                    "model": "auto",
                    "messages": [{"role": "user", "content": "hello"}],
                },
            )

        self.assertEqual(response.status_code, 200)
        self.assertIsNotNone(response.json()["polygate"]["failover_from"])
        cache_set.assert_not_called()

    def test_forced_provider_without_tools_is_rejected_before_call(self):
        legacy_provider = {
            "name": "legacy-text",
            "kind": "mock",
            "privacy": "internal",
            "price_per_1k_input": 0.0,
            "price_per_1k_output": 0.0,
            "typical_latency_ms": 1,
            "capabilities": {"streaming": True, "tools": False},
        }
        payload = pi_payload()
        payload["model"] = "legacy-text"
        payload["parallel_tool_calls"] = False
        with patch("app.main.PROVIDERS", [legacy_provider]):
            response = client.post("/v1/chat/completions", json=payload)

        self.assertEqual(response.status_code, 400)
        self.assertIn("tools", response.json()["detail"])

    def test_stream_fails_over_only_before_first_event(self):
        base = {
            "id": "chatcmpl-fallback",
            "object": "chat.completion.chunk",
            "created": 1,
            "model": "mock-fast",
        }
        first = data_event({
            **base,
            "choices": [{"index": 0, "delta": {"role": "assistant", "content": "ok"}, "finish_reason": None}],
        })

        async def remaining():
            yield data_event({
                **base,
                "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 2, "completion_tokens": 1, "total_tokens": 3},
            })
            yield b"data: [DONE]\n\n"

        opened = OpenedProviderStream(None, first, remaining(), 2)
        async_open = AsyncMock(side_effect=[RuntimeError("before first event"), opened])
        payload = {
            "model": "auto",
            "messages": [{"role": "user", "content": "hello"}],
            "stream": True,
        }
        with patch("app.main.open_provider_stream_with_resilience", async_open):
            response = client.post("/v1/chat/completions", json=payload)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(async_open.await_count, 2)
        self.assertEqual(response.headers["x-polygate-provider"], "mock-a")
        self.assertIn("data: [DONE]", response.text)

    def test_stream_does_not_splice_provider_after_first_event(self):
        base = {
            "id": "chatcmpl-partial",
            "object": "chat.completion.chunk",
            "created": 1,
            "model": "mock-cheap",
        }
        first = data_event({
            **base,
            "choices": [{"index": 0, "delta": {"role": "assistant", "content": "partial"}, "finish_reason": None}],
        })

        async def broken_remaining():
            if False:
                yield b""
            raise RuntimeError("stream broke after first event")

        opened = OpenedProviderStream(None, first, broken_remaining(), 2)
        async_open = AsyncMock(return_value=opened)
        payload = {
            "model": "auto",
            "messages": [{"role": "user", "content": "hello"}],
            "stream": True,
        }
        with patch("app.main.open_provider_stream_with_resilience", async_open):
            response = client.post("/v1/chat/completions", json=payload)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(async_open.await_count, 1)
        self.assertIn("partial", response.text)
        self.assertNotIn("data: [DONE]", response.text)

    def test_truncated_stream_counts_as_a_breaker_failure(self):
        base = {
            "id": "chatcmpl-truncated",
            "object": "chat.completion.chunk",
            "created": 1,
            "model": "mock-cheap",
        }
        first = data_event({
            **base,
            "choices": [{
                "index": 0,
                "delta": {"role": "assistant", "content": "partial"},
                "finish_reason": None,
            }],
        })

        async def truncated_remaining():
            if False:
                yield b""

        opened = OpenedProviderStream(None, first, truncated_remaining(), 2)
        breaker = CircuitBreakerRegistry(failure_threshold=1)
        with (
            patch("app.main.BREAKER", breaker),
            patch(
                "app.main.open_provider_stream_with_resilience",
                AsyncMock(return_value=opened),
            ),
        ):
            response = client.post(
                "/v1/chat/completions",
                json={
                    "model": "auto",
                    "messages": [{"role": "user", "content": "hello"}],
                    "stream": True,
                },
            )

        provider = response.headers["x-polygate-provider"]
        self.assertEqual(response.status_code, 200)
        self.assertEqual(breaker.health_snapshot()[provider], "down")

    def test_usage_only_stream_event_is_hidden_unless_requested(self):
        base = {
            "id": "chatcmpl-usage",
            "object": "chat.completion.chunk",
            "created": 1,
            "model": "mock-cheap",
        }
        first = data_event({
            **base,
            "choices": [{
                "index": 0,
                "delta": {"role": "assistant", "content": "ok"},
                "finish_reason": None,
            }],
        })
        usage = data_event({
            **base,
            "choices": [],
            "usage": {"prompt_tokens": 2, "completion_tokens": 1, "total_tokens": 3},
        })

        def opened_stream():
            async def remaining():
                yield data_event({
                    **base,
                    "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
                })
                yield usage
                yield b"data: [DONE]\n\n"

            return OpenedProviderStream(None, first, remaining(), 2)

        async_open = AsyncMock(side_effect=[opened_stream(), opened_stream()])
        payload = {
            "model": "auto",
            "messages": [{"role": "user", "content": "hello"}],
            "stream": True,
        }
        with patch("app.main.open_provider_stream_with_resilience", async_open):
            hidden = client.post("/v1/chat/completions", json=payload)
            visible = client.post(
                "/v1/chat/completions",
                json={
                    **payload,
                    "stream_options": {"include_usage": True},
                },
            )

        self.assertNotIn('"usage":', hidden.text)
        self.assertIn('"usage":', visible.text)

    def test_sse_error_event_is_rejected_before_downstream_commit(self):
        with self.assertRaises(ProviderProtocolError):
            _is_completion_event(
                {"name": "broken"},
                data_event({"error": {"message": "upstream failed"}}),
            )

    def test_unknown_top_level_field_returns_diagnostic_error(self):
        payload = pi_payload()
        payload["silently_ignored_option"] = True
        response = client.post("/v1/chat/completions", json=payload)

        self.assertEqual(response.status_code, 422)
        error = response.json()["error"]
        self.assertEqual(error["code"], "request_validation_error")
        self.assertIn("silently_ignored_option", str(error["details"]))


class StreamRetryBreakerTests(unittest.IsolatedAsyncioTestCase):
    async def test_opening_stream_does_not_clear_existing_failure_history(self):
        provider = {"name": "mock-a"}
        breaker = CircuitBreakerRegistry(failure_threshold=2)
        breaker.record_failure(provider["name"])

        async def remaining():
            if False:
                yield b""

        opened = OpenedProviderStream(
            None,
            data_event({"choices": [{"index": 0, "delta": {}}]}),
            remaining(),
            1,
        )
        with patch("app.retry.open_provider_stream", AsyncMock(return_value=opened)):
            await open_provider_stream_with_resilience(provider, {}, breaker)

        breaker.record_failure(provider["name"])
        self.assertEqual(breaker.health_snapshot()[provider["name"]], "down")


class ResilienceClassificationTests(unittest.TestCase):
    def test_local_payload_error_does_not_open_provider_breaker(self):
        provider = {"name": "mock-a"}
        breaker = CircuitBreakerRegistry(failure_threshold=1)

        def incompatible(*_args, **_kwargs):
            raise ProviderPayloadError("request cannot be represented")

        with self.assertRaises(ProviderPayloadError):
            call_provider_with_resilience(
                provider,
                {},
                breaker,
                provider_call=incompatible,
            )

        self.assertEqual(breaker.health_snapshot()[provider["name"]], "healthy")

    def test_network_read_error_is_retried(self):
        provider = {"name": "mock-a"}
        breaker = CircuitBreakerRegistry()
        response = AdapterResult({
            "choices": [{"message": {"content": "ok"}}],
            "usage": {},
        }, 1)
        attempts = [
            httpx.ReadError("connection reset"),
            response,
        ]

        def flaky(*_args, **_kwargs):
            result = attempts.pop(0)
            if isinstance(result, Exception):
                raise result
            return result

        result = call_provider_with_resilience(
            provider,
            {},
            breaker,
            base_delay_s=0,
            provider_call=flaky,
        )

        self.assertIs(result, response)
        self.assertEqual(result.retries, 1)


if __name__ == "__main__":
    unittest.main()
