"""Controllable OpenAI-compatible mock with an Agent tool-call loop."""
from __future__ import annotations

import asyncio
import json
import os
import random
import re
import time
from typing import Any, AsyncIterator

from fastapi import FastAPI, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel


NAME = os.environ.get("MOCK_NAME", "mock")
DEFAULT_TOOL_PATH = os.environ.get("MOCK_TOOL_PATH", "/tmp/polygate-agent-test.txt")
STREAM_CHUNK_DELAY_MS = int(os.environ.get("MOCK_STREAM_CHUNK_DELAY_MS", "0"))
app = FastAPI(title=f"PolyGate Mock Provider [{NAME}]")

STATE = {"fail_rate": 0.0, "extra_latency_ms": 0}


class AdminConfig(BaseModel):
    fail_rate: float | None = None
    extra_latency_ms: int | None = None


@app.get("/health")
def health():
    return {"status": "ok", "name": NAME, "config": STATE}


@app.post("/admin/config")
def set_config(cfg: AdminConfig):
    if cfg.fail_rate is not None:
        STATE["fail_rate"] = max(0.0, min(1.0, cfg.fail_rate))
    if cfg.extra_latency_ms is not None:
        STATE["extra_latency_ms"] = max(0, cfg.extra_latency_ms)
    return {"ok": True, "config": STATE}


@app.post("/admin/reset")
def reset():
    STATE.update({"fail_rate": 0.0, "extra_latency_ms": 0})
    return {"ok": True, "config": STATE}


def _content_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ""
    return "".join(
        block.get("text", "")
        for block in content
        if isinstance(block, dict) and isinstance(block.get("text"), str)
    )


def _usage(messages: list[dict], completion: str) -> dict[str, int]:
    prompt_text = "".join(_content_text(message.get("content")) for message in messages)
    prompt_tokens = max(1, len(prompt_text) // 4)
    completion_tokens = max(1, len(completion) // 4)
    return {
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "total_tokens": prompt_tokens + completion_tokens,
    }


def _test_file(messages: list[dict]) -> str:
    text = "\n".join(_content_text(message.get("content")) for message in messages)
    match = re.search(r"POLYGATE_TEST_FILE=([^\s]+)", text)
    return match.group(1) if match else DEFAULT_TOOL_PATH


def _tool_name(tools: list[dict]) -> str | None:
    for tool in tools:
        function = tool.get("function") if isinstance(tool, dict) else None
        if isinstance(function, dict) and isinstance(function.get("name"), str):
            return function["name"]
    return None


def _tool_arguments(name: str, messages: list[dict]) -> str:
    if name == "read":
        return json.dumps({"path": _test_file(messages)}, separators=(",", ":"))
    return "{}"


def _data_event(payload: dict) -> bytes:
    data = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    return f"data: {data}\n\n".encode("utf-8")


def _base_chunk(completion_id: str, model: str) -> dict:
    return {
        "id": completion_id,
        "object": "chat.completion.chunk",
        "created": int(time.time()),
        "model": model,
    }


async def _events(events: list[bytes]) -> AsyncIterator[bytes]:
    for event in events:
        if STREAM_CHUNK_DELAY_MS:
            await asyncio.sleep(STREAM_CHUNK_DELAY_MS / 1000)
        yield event


def _tool_stream(
    completion_id: str,
    model: str,
    messages: list[dict],
    tool_name: str,
) -> list[bytes]:
    base = _base_chunk(completion_id, model)
    arguments = _tool_arguments(tool_name, messages)
    split_at = max(1, len(arguments) // 2)
    call_id = f"call_{NAME}_{random.randint(1000, 9999)}"
    usage = _usage(messages, arguments)
    return [
        _data_event({
            **base,
            "choices": [{
                "index": 0,
                "delta": {
                    "role": "assistant",
                    "tool_calls": [{
                        "index": 0,
                        "id": call_id,
                        "type": "function",
                        "function": {"name": tool_name, "arguments": ""},
                    }],
                },
                "finish_reason": None,
            }],
        }),
        _data_event({
            **base,
            "choices": [{
                "index": 0,
                "delta": {"tool_calls": [{"index": 0, "function": {"arguments": arguments[:split_at]}}]},
                "finish_reason": None,
            }],
        }),
        _data_event({
            **base,
            "choices": [{
                "index": 0,
                "delta": {"tool_calls": [{"index": 0, "function": {"arguments": arguments[split_at:]}}]},
                "finish_reason": None,
            }],
        }),
        _data_event({
            **base,
            "choices": [{"index": 0, "delta": {}, "finish_reason": "tool_calls"}],
        }),
        _data_event({**base, "choices": [], "usage": usage}),
        b"data: [DONE]\n\n",
    ]


def _text_stream(
    completion_id: str,
    model: str,
    messages: list[dict],
    content: str,
) -> list[bytes]:
    base = _base_chunk(completion_id, model)
    midpoint = max(1, len(content) // 2)
    return [
        _data_event({
            **base,
            "choices": [{"index": 0, "delta": {"role": "assistant", "content": content[:midpoint]}, "finish_reason": None}],
        }),
        _data_event({
            **base,
            "choices": [{"index": 0, "delta": {"content": content[midpoint:]}, "finish_reason": None}],
        }),
        _data_event({
            **base,
            "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
        }),
        _data_event({**base, "choices": [], "usage": _usage(messages, content)}),
        b"data: [DONE]\n\n",
    ]


@app.post("/v1/chat/completions")
async def chat_completions(body: dict):
    if STATE["extra_latency_ms"]:
        await asyncio.sleep(STATE["extra_latency_ms"] / 1000)
    if random.random() < STATE["fail_rate"]:
        raise HTTPException(status_code=500, detail=f"[{NAME}] injected failure")

    messages = body.get("messages", [])
    tools = body.get("tools") or []
    model = body.get("model", f"mock-{NAME}")
    completion_id = f"chatcmpl-{NAME}-{random.randint(1000, 9999)}"
    has_tool_result = any(message.get("role") == "tool" for message in messages)
    tool_name = _tool_name(tools)

    if tool_name and not has_tool_result:
        if body.get("stream"):
            return StreamingResponse(
                _events(_tool_stream(completion_id, model, messages, tool_name)),
                media_type="text/event-stream",
                headers={"Cache-Control": "no-cache"},
            )
        arguments = _tool_arguments(tool_name, messages)
        return {
            "id": completion_id,
            "object": "chat.completion",
            "created": int(time.time()),
            "model": model,
            "choices": [{
                "index": 0,
                "message": {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [{
                        "id": f"call_{NAME}_{random.randint(1000, 9999)}",
                        "type": "function",
                        "function": {"name": tool_name, "arguments": arguments},
                    }],
                },
                "finish_reason": "tool_calls",
            }],
            "usage": _usage(messages, arguments),
        }

    user_text = next(
        (_content_text(message.get("content")) for message in reversed(messages) if message.get("role") == "user"),
        "",
    )
    content = "mock ok" if has_tool_result else f"Hello World from {NAME}! You said: {user_text[:120]}"
    if body.get("stream"):
        return StreamingResponse(
            _events(_text_stream(completion_id, model, messages, content)),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache"},
        )
    return {
        "id": completion_id,
        "object": "chat.completion",
        "created": int(time.time()),
        "model": model,
        "choices": [{
            "index": 0,
            "message": {"role": "assistant", "content": content},
            "finish_reason": "stop",
        }],
        "usage": _usage(messages, content),
    }
