"""Provider request adaptation for JSON and SSE Chat Completions."""
from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from typing import Any, AsyncIterator

import httpx


class ProviderPayloadError(ValueError):
    """The selected provider cannot represent part of the canonical request."""


class ProviderProtocolError(RuntimeError):
    """The upstream returned a successful HTTP response with an invalid body."""


class AdapterResult:
    def __init__(self, response: dict[str, Any], latency_ms: int):
        self.response = response
        self.latency_ms = latency_ms
        usage = response.get("usage") or {}
        self.input_tokens = int(usage.get("prompt_tokens", 0))
        self.output_tokens = int(usage.get("completion_tokens", 0))
        self.retries = 0

    @property
    def content(self) -> str:
        choices = self.response.get("choices") or []
        if not choices:
            return ""
        content = (choices[0].get("message") or {}).get("content")
        return content if isinstance(content, str) else ""


@dataclass
class OpenedProviderStream:
    response: httpx.Response | None
    first_event: bytes
    remaining_events: AsyncIterator[bytes]
    latency_to_first_event_ms: int
    retries: int = 0

    async def aclose(self) -> None:
        if self.response is not None:
            await self.response.aclose()


_ASYNC_CLIENT: httpx.AsyncClient | None = None


async def startup_async_client() -> None:
    global _ASYNC_CLIENT
    if _ASYNC_CLIENT is None:
        _ASYNC_CLIENT = httpx.AsyncClient(
            limits=httpx.Limits(max_connections=200, max_keepalive_connections=40),
            follow_redirects=False,
        )


async def shutdown_async_client() -> None:
    global _ASYNC_CLIENT
    if _ASYNC_CLIENT is not None:
        await _ASYNC_CLIENT.aclose()
        _ASYNC_CLIENT = None


async def get_async_client() -> httpx.AsyncClient:
    if _ASYNC_CLIENT is None:
        await startup_async_client()
    assert _ASYNC_CLIENT is not None
    return _ASYNC_CLIENT


def _message_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ""
    return "".join(
        block.get("text", "")
        for block in content
        if isinstance(block, dict)
        and block.get("type") in {"text", "input_text"}
        and isinstance(block.get("text"), str)
    )


def _normalize_messages(provider: dict, messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    capabilities = provider.get("capabilities", {})
    accepts_blocks = capabilities.get("content_blocks") is True
    accepts_developer = capabilities.get("developer_role") is True
    normalized: list[dict[str, Any]] = []

    for source in messages:
        message = dict(source)
        if message.get("role") == "developer" and not accepts_developer:
            message["role"] = "system"

        content = message.get("content")
        if isinstance(content, list) and not accepts_blocks:
            unsupported = [
                block.get("type")
                for block in content
                if not isinstance(block, dict)
                or block.get("type") not in {"text", "input_text"}
            ]
            if unsupported:
                raise ProviderPayloadError(
                    f"provider {provider['name']} cannot accept content blocks: {unsupported}"
                )
            message["content"] = _message_text(content)
        normalized.append(message)

    return normalized


def build_provider_payload(provider: dict, payload: dict[str, Any]) -> dict[str, Any]:
    """Replace the virtual model and adapt only provider-specific differences."""
    body = dict(payload)
    body.pop("polygate", None)
    body["model"] = provider.get("model", "default")
    body["messages"] = _normalize_messages(provider, list(body.get("messages", [])))
    capabilities = provider.get("capabilities", {})
    if "store" in body and capabilities.get("store") is not True:
        if body["store"] is True:
            raise ProviderPayloadError(
                f"provider {provider['name']} cannot honor store=true"
            )
        # store=false is the default and can be omitted without changing semantics.
        body.pop("store", None)
    if body.get("stream"):
        stream_options = dict(body.get("stream_options") or {})
        stream_options.setdefault("include_usage", True)
        body["stream_options"] = stream_options
    return body


def _provider_headers(provider: dict) -> dict[str, str]:
    headers = {"Content-Type": "application/json", "Accept": "application/json"}
    key_env = provider.get("api_key_env")
    if key_env:
        api_key = os.environ.get(key_env)
        if not api_key:
            raise RuntimeError(f"provider credential environment variable {key_env} is not configured")
        headers["Authorization"] = f"Bearer {api_key}"
    return headers


def _fake_call(provider: dict, payload: dict[str, Any]) -> AdapterResult:
    messages = payload.get("messages", [])
    user_text = next(
        (_message_text(m.get("content")) for m in reversed(messages) if m.get("role") == "user"),
        "",
    )
    content = f"[fake:{provider['name']}] echo> {user_text[:80]}"
    response = {
        "id": f"chatcmpl-fake-{provider['name']}",
        "object": "chat.completion",
        "model": provider.get("model", "default"),
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": content},
                "finish_reason": "stop",
            }
        ],
        "usage": {
            "prompt_tokens": max(1, len(user_text) // 4),
            "completion_tokens": max(1, len(content) // 4),
        },
    }
    result = AdapterResult(response, provider.get("typical_latency_ms", 200))
    time.sleep(result.latency_ms / 1000.0 * 0.1)
    return result


def call_provider(
    provider: dict,
    payload: dict[str, Any],
    timeout_s: float = 30.0,
) -> AdapterResult:
    """Synchronous JSON path retained for the existing Web client."""
    if os.environ.get("FAKE_ADAPTER") == "1":
        return _fake_call(provider, payload)

    body = build_provider_payload(provider, payload)
    body["stream"] = False
    started = time.perf_counter()
    response = httpx.post(
        provider["endpoint"],
        json=body,
        headers=_provider_headers(provider),
        timeout=timeout_s,
    )
    response.raise_for_status()
    data = response.json()
    if not isinstance(data.get("choices"), list):
        raise ProviderProtocolError(f"provider {provider['name']} returned no choices")
    return AdapterResult(data, int((time.perf_counter() - started) * 1000))


async def _iter_sse_events(response: httpx.Response) -> AsyncIterator[bytes]:
    lines: list[str] = []
    async for line in response.aiter_lines():
        if line == "":
            if lines:
                yield ("\n".join(lines) + "\n\n").encode("utf-8")
                lines = []
            continue
        lines.append(line)
    if lines:
        yield ("\n".join(lines) + "\n\n").encode("utf-8")


async def _fake_stream(provider: dict, payload: dict[str, Any]) -> OpenedProviderStream:
    result = _fake_call(provider, payload)
    chunk_base = {
        "id": result.response["id"],
        "object": "chat.completion.chunk",
        "created": int(time.time()),
        "model": result.response["model"],
    }
    events = [
        _data_event({
            **chunk_base,
            "choices": [{"index": 0, "delta": {"role": "assistant", "content": result.content}, "finish_reason": None}],
        }),
        _data_event({
            **chunk_base,
            "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
            "usage": result.response["usage"],
        }),
        b"data: [DONE]\n\n",
    ]

    async def remaining() -> AsyncIterator[bytes]:
        for event in events[1:]:
            yield event

    return OpenedProviderStream(None, events[0], remaining(), result.latency_ms)


def _data_event(data: dict[str, Any]) -> bytes:
    return f"data: {json.dumps(data, ensure_ascii=False, separators=(',', ':'))}\n\n".encode("utf-8")


async def open_provider_stream(
    provider: dict,
    payload: dict[str, Any],
    timeout_s: float = 90.0,
) -> OpenedProviderStream:
    """Open an upstream stream and prefetch its first SSE event.

    Prefetching is what makes a retry/fallback possible before FastAPI commits
    the downstream 200 response headers.
    """
    if os.environ.get("FAKE_ADAPTER") == "1":
        return await _fake_stream(provider, payload)

    client = await get_async_client()
    body = build_provider_payload(provider, payload)
    body["stream"] = True
    request = client.build_request(
        "POST",
        provider["endpoint"],
        json=body,
        headers={**_provider_headers(provider), "Accept": "text/event-stream"},
        timeout=httpx.Timeout(timeout_s, connect=5.0, write=30.0, pool=5.0),
    )
    started = time.perf_counter()
    response = await client.send(request, stream=True)
    if response.is_error:
        await response.aread()
        try:
            response.raise_for_status()
        finally:
            await response.aclose()

    content_type = response.headers.get("content-type", "")
    if "text/event-stream" not in content_type.lower():
        await response.aread()
        await response.aclose()
        raise ProviderProtocolError(
            f"provider {provider['name']} returned {content_type or 'no content type'} for stream=true"
        )

    events = _iter_sse_events(response)
    try:
        first_event = await anext(events)
    except StopAsyncIteration as exc:
        await response.aclose()
        raise ProviderProtocolError(f"provider {provider['name']} returned an empty SSE stream") from exc

    return OpenedProviderStream(
        response=response,
        first_event=first_event,
        remaining_events=events,
        latency_to_first_event_ms=int((time.perf_counter() - started) * 1000),
    )
