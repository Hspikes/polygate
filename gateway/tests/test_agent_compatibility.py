"""Agent request, SSE, cache and authentication regression tests."""
import json
import os
from pathlib import Path
import unittest
from unittest.mock import AsyncMock, patch


PROJECT_ROOT = Path(__file__).resolve().parents[2]
os.environ["PROVIDERS_FILE"] = str(PROJECT_ROOT / "contracts" / "providers.yaml")
os.environ["FAKE_ADAPTER"] = "1"

from fastapi.testclient import TestClient  # noqa: E402

from app.adapters import OpenedProviderStream, build_provider_payload  # noqa: E402
from app.main import CACHE, app  # noqa: E402
from app.models import GatewayRequest  # noqa: E402


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

    def test_unknown_top_level_field_returns_diagnostic_error(self):
        payload = pi_payload()
        payload["silently_ignored_option"] = True
        response = client.post("/v1/chat/completions", json=payload)

        self.assertEqual(response.status_code, 422)
        error = response.json()["error"]
        self.assertEqual(error["code"], "request_validation_error")
        self.assertIn("silently_ignored_option", str(error["details"]))


if __name__ == "__main__":
    unittest.main()
