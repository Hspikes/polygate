"""Provider request adaptation for JSON and SSE Chat Completions."""
from __future__ import annotations

import json
import os
import time
from copy import deepcopy
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
    request_defaults = provider.get("request_defaults") or {}
    if not isinstance(request_defaults, dict):
        raise ProviderPayloadError(
            f"provider {provider['name']} has invalid request_defaults"
        )
    # Defaults are trusted registry metadata. The canonical client payload wins
    # for standard fields, while vendor-only options (for example DeepSeek V4's
    # thinking toggle) can be supplied without widening the public API.
    body = deepcopy(request_defaults)
    body.update(payload)
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
        # Always request usage for gateway accounting. The downstream layer
        # removes the usage-only event again when the client did not ask for
        # it, preserving the public Chat Completions contract.
        stream_options["include_usage"] = True
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
    try:
        data = response.json()
    except (TypeError, ValueError) as exc:
        raise ProviderProtocolError(
            f"provider {provider['name']} returned malformed JSON"
        ) from exc
    if not isinstance(data, dict) or not isinstance(data.get("choices"), list):
        raise ProviderProtocolError(f"provider {provider['name']} returned no choices")
    try:
        return AdapterResult(data, int((time.perf_counter() - started) * 1000))
    except (TypeError, ValueError) as exc:
        raise ProviderProtocolError(
            f"provider {provider['name']} returned invalid usage data"
        ) from exc


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


def _event_data(event: bytes) -> str | None:
    """Return one SSE event's joined data field, ignoring comments/metadata."""
    data_lines: list[str] = []
    for raw_line in event.decode("utf-8", errors="replace").splitlines():
        if not raw_line.startswith("data:"):
            continue
        value = raw_line[5:]
        if value.startswith(" "):
            value = value[1:]
        data_lines.append(value)
    return "\n".join(data_lines) if data_lines else None


def _is_completion_event(provider: dict, event: bytes) -> bool:
    """Validate the first semantic upstream event before committing HTTP 200."""
    data = _event_data(event)
    if data is None:
        return False
    if data.strip() == "[DONE]":
        raise ProviderProtocolError(
            f"provider {provider['name']} ended before returning a completion chunk"
        )
    try:
        payload = json.loads(data)
    except json.JSONDecodeError as exc:
        raise ProviderProtocolError(
            f"provider {provider['name']} returned malformed SSE data"
        ) from exc
    if not isinstance(payload, dict):
        raise ProviderProtocolError(
            f"provider {provider['name']} returned a non-object SSE payload"
        )
    if payload.get("error") is not None:
        raise ProviderProtocolError(
            f"provider {provider['name']} returned an SSE error before its first chunk"
        )
    choices = payload.get("choices")
    if not isinstance(choices, list) or not choices:
        raise ProviderProtocolError(
            f"provider {provider['name']} returned no choices in its first SSE payload"
        )
    return True


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
        }),
        _data_event({
            **chunk_base,
            "choices": [],
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
        try:
            await response.aread()
            response.raise_for_status()
        finally:
            await response.aclose()

    content_type = response.headers.get("content-type", "")
    if "text/event-stream" not in content_type.lower():
        try:
            await response.aread()
        finally:
            await response.aclose()
        raise ProviderProtocolError(
            f"provider {provider['name']} returned {content_type or 'no content type'} for stream=true"
        )

    events = _iter_sse_events(response)
    prefetched: list[bytes] = []
    prefetched_size = 0
    try:
        for _ in range(32):
            event = await anext(events)
            prefetched.append(event)
            prefetched_size += len(event)
            if prefetched_size > 64 * 1024:
                raise ProviderProtocolError(
                    f"provider {provider['name']} sent too much SSE prelude data"
                )
            if _is_completion_event(provider, event):
                break
        else:
            raise ProviderProtocolError(
                f"provider {provider['name']} sent too many SSE prelude events"
            )
    except StopAsyncIteration as exc:
        await response.aclose()
        raise ProviderProtocolError(f"provider {provider['name']} returned an empty SSE stream") from exc
    # A request-budget cancellation is a BaseException on supported Python
    # versions; always release the checked-out streaming connection.
    except BaseException:
        await response.aclose()
        raise

    return OpenedProviderStream(
        response=response,
        first_event=b"".join(prefetched),
        remaining_events=events,
        latency_to_first_event_ms=int((time.perf_counter() - started) * 1000),
    )
