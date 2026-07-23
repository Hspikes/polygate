"""The mock Provider can drive a complete two-request Agent tool loop."""
import json
import unittest

from fastapi.testclient import TestClient

from app.main import STATE, app


client = TestClient(app)


def sse_payloads(body: str) -> list[dict | str]:
    values: list[dict | str] = []
    for line in body.splitlines():
        if not line.startswith("data:"):
            continue
        data = line[5:].strip()
        values.append(data if data == "[DONE]" else json.loads(data))
    return values


class MockAgentStreamTests(unittest.TestCase):
    def setUp(self):
        STATE.update({"fail_rate": 0.0, "extra_latency_ms": 0})

    def test_streamed_tool_call_then_final_text(self):
        messages = [{
            "role": "user",
            "content": [{"type": "text", "text": "POLYGATE_TEST_FILE=/tmp/fixture.txt"}],
        }]
        tools = [{
            "type": "function",
            "function": {
                "name": "read",
                "parameters": {"type": "object", "properties": {"path": {"type": "string"}}},
            },
        }]
        first = client.post(
            "/v1/chat/completions",
            json={"model": "mock", "messages": messages, "tools": tools, "stream": True},
        )

        self.assertEqual(first.status_code, 200)
        self.assertTrue(first.headers["content-type"].startswith("text/event-stream"))
        first_events = sse_payloads(first.text)
        chunks = [event for event in first_events if isinstance(event, dict)]
        finish_reasons = [
            choice.get("finish_reason")
            for chunk in chunks
            for choice in chunk.get("choices", [])
        ]
        self.assertIn("tool_calls", finish_reasons)
        self.assertEqual(first_events[-1], "[DONE]")

        tool_calls = [
            call
            for chunk in chunks
            for choice in chunk.get("choices", [])
            for call in choice.get("delta", {}).get("tool_calls", [])
        ]
        arguments = "".join(call.get("function", {}).get("arguments", "") for call in tool_calls)
        self.assertEqual(json.loads(arguments), {"path": "/tmp/fixture.txt"})

        call_id = next(call["id"] for call in tool_calls if call.get("id"))
        second_messages = [
            *messages,
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [{
                    "id": call_id,
                    "type": "function",
                    "function": {"name": "read", "arguments": arguments},
                }],
            },
            {"role": "tool", "tool_call_id": call_id, "content": "fixture contents"},
        ]
        second = client.post(
            "/v1/chat/completions",
            json={"model": "mock", "messages": second_messages, "tools": tools, "stream": True},
        )

        self.assertEqual(second.status_code, 200)
        second_events = sse_payloads(second.text)
        second_chunks = [event for event in second_events if isinstance(event, dict)]
        final_text = "".join(
            choice.get("delta", {}).get("content", "")
            for chunk in second_chunks
            for choice in chunk.get("choices", [])
        )
        finish_reasons = [
            choice.get("finish_reason")
            for chunk in second_chunks
            for choice in chunk.get("choices", [])
        ]
        self.assertEqual(final_text, "mock ok")
        self.assertIn("stop", finish_reasons)
        self.assertTrue(second.text.rstrip().endswith("data: [DONE]"))


if __name__ == "__main__":
    unittest.main()
