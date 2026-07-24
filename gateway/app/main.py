"""PolyGate Gateway: Web-compatible JSON and Agent-compatible SSE routing."""
from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import time
import uuid
from typing import Any, AsyncIterator

from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse
from prometheus_client import CONTENT_TYPE_LATEST
from starlette.concurrency import run_in_threadpool

from app.adapters import (
    ProviderProtocolError,
    call_provider,
    shutdown_async_client,
    startup_async_client,
)
from app.auth import require_client_api_key
from app.cache import Cache, cache_key
from app.circuit_breaker import CircuitBreakerRegistry
from app.cost import estimate_cost
from app.health_checker import health_check_loop
from app.metrics import (
    record_cache,
    record_provider,
    record_request,
    record_usage,
    render_metrics,
)
from app.models import DecisionCard, GatewayRequest, Tokens
from app.registry import load_providers
from app.retry import call_provider_with_resilience, open_provider_stream_with_resilience
from app.router import missing_capabilities, select_provider


logging.basicConfig(level=logging.INFO, format='{"ts":"%(asctime)s","lvl":"%(levelname)s","msg":%(message)s}')
log = logging.getLogger("polygate")

app = FastAPI(title="PolyGate Gateway", version="0.3.0")
CORS_ALLOW_ORIGINS = [
    origin.strip()
    for origin in os.environ.get(
        "CORS_ALLOW_ORIGINS",
        "http://localhost:8080,http://127.0.0.1:8080",
    ).split(",")
    if origin.strip()
]
if CORS_ALLOW_ORIGINS:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=CORS_ALLOW_ORIGINS,
        allow_methods=["GET", "POST", "OPTIONS"],
        allow_headers=[
            "Content-Type",
            "Authorization",
            "X-PolyGate-Session-ID",
        ],
        expose_headers=["X-PolyGate-Request-ID", "X-PolyGate-Provider"],
    )

PROVIDERS = load_providers()
BREAKER = CircuitBreakerRegistry()
CACHE = Cache()
_HEALTH_TASK: asyncio.Task | None = None


@app.on_event("startup")
async def startup_event() -> None:
    global _HEALTH_TASK
    await startup_async_client()
    probeable = [provider for provider in PROVIDERS if provider.get("kind") != "real"]
    if probeable and _HEALTH_TASK is None:
        _HEALTH_TASK = asyncio.create_task(health_check_loop(probeable, BREAKER))


@app.on_event("shutdown")
async def shutdown_event() -> None:
    global _HEALTH_TASK
    if _HEALTH_TASK is not None:
        _HEALTH_TASK.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await _HEALTH_TASK
        _HEALTH_TASK = None
    await shutdown_async_client()


@app.exception_handler(RequestValidationError)
async def validation_error_handler(_request: Request, exc: RequestValidationError):
    details = []
    for error in exc.errors():
        detail = {key: value for key, value in error.items() if key != "input"}
        if "ctx" in detail:
            detail["ctx"] = {
                key: str(value) for key, value in detail["ctx"].items()
            }
        details.append(detail)
    return JSONResponse(
        status_code=422,
        content={
            "error": {
                "message": "invalid Chat Completions request",
                "type": "invalid_request_error",
                "code": "request_validation_error",
                "details": details,
            }
        },
    )


@app.middleware("http")
async def record_chat_request(request: Request, call_next):
    """Record non-stream requests here; stream generators record at EOF."""
    if request.method != "POST" or request.url.path != "/v1/chat/completions":
        return await call_next(request)

    started = time.perf_counter()
    request.state.metric_started = started
    try:
        response = await call_next(request)
    except Exception:
        record_request("server_error", time.perf_counter() - started)
        raise

    if getattr(request.state, "defer_metrics_to_stream", False):
        return response

    outcome = getattr(request.state, "metric_outcome", None)
    if outcome is None:
        if 400 <= response.status_code < 500:
            outcome = "client_error"
        elif response.status_code >= 500:
            outcome = "server_error"
        else:
            outcome = "success"
    record_request(outcome, time.perf_counter() - started)
    return response


@app.get("/health")
def health():
    return {
        "status": "ok",
        "providers": [provider["name"] for provider in PROVIDERS],
        "cache": CACHE.enabled,
    }


@app.get("/metrics", include_in_schema=False)
def metrics():
    breaker_states = BREAKER.state_snapshot()
    provider_states = {
        provider["name"]: breaker_states.get(provider["name"], "closed")
        for provider in PROVIDERS
    }
    return Response(
        content=render_metrics(provider_states),
        media_type=CONTENT_TYPE_LATEST,
    )


@app.get("/providers")
def providers():
    return [
        {
            "name": provider["name"],
            "kind": provider.get("kind"),
            "privacy": provider.get("privacy"),
            "price_per_1k_input": provider.get("price_per_1k_input"),
            "price_per_1k_output": provider.get("price_per_1k_output"),
            "typical_latency_ms": provider.get("typical_latency_ms"),
            "capabilities": provider.get("capabilities", {}),
            "health": BREAKER.debug_snapshot().get(provider["name"], "healthy"),
        }
        for provider in PROVIDERS
    ]


@app.get("/v1/models")
def models(_authenticated: None = Depends(require_client_api_key)):
    return {
        "object": "list",
        "data": [
            {
                "id": "auto",
                "object": "model",
                "owned_by": "polygate",
            }
        ],
    }


def _forced_provider(req: GatewayRequest, required_capabilities: set[str]) -> dict | None:
    if req.model == "auto":
        return None
    forced = next((provider for provider in PROVIDERS if provider["name"] == req.model), None)
    if forced is None:
        raise HTTPException(status_code=400, detail=f"未知的 provider: {req.model}")
    if req.polygate.privacy == "high" and forced.get("privacy") == "external":
        raise HTTPException(
            status_code=403,
            detail=f"privacy=high 禁止强制路由到外部 Provider {forced['name']}",
        )
    missing = missing_capabilities(forced, required_capabilities)
    if missing:
        raise HTTPException(
            status_code=400,
            detail=(
                f"provider {forced['name']} 不支持请求所需能力: "
                + ", ".join(sorted(missing))
            ),
        )
    return forced


def _select(
    candidates: list[dict],
    messages: list[dict],
    req: GatewayRequest,
    required_capabilities: set[str],
) -> tuple[dict, str]:
    try:
        chosen, reason, _ = select_provider(
            candidates,
            messages,
            req.polygate,
            BREAKER.health_snapshot(),
            required_capabilities,
        )
        return chosen, reason
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@app.post("/v1/chat/completions")
async def chat_completions(
    req: GatewayRequest,
    request: Request,
    _authenticated: None = Depends(require_client_api_key),
):
    request_id = "req_" + uuid.uuid4().hex
    constraints = req.polygate
    messages = req.message_dicts()
    payload = req.provider_payload()
    required_capabilities = req.required_capabilities()
    started = time.perf_counter()

    forced = _forced_provider(req, required_capabilities)

    key: str | None = None
    if req.bypass_cache():
        record_cache("bypass")
    else:
        cache_scope = forced["name"] if forced else "auto"
        key = cache_key(
            messages,
            constraints.privacy,
            cache_scope,
            constraints.quality,
            constraints.max_cost_usd,
            constraints.latency_target_ms,
        )
        cached = CACHE.get(key)
        if cached:
            record_cache("hit")
            request.state.metric_outcome = "cache_hit"
            latency_ms = int((time.perf_counter() - started) * 1000)
            card = DecisionCard(
                chosen_provider="cache",
                reason="精确缓存命中，未调用任何 Provider，成本为 0",
                cache_hit=True,
                cost_estimate_usd=0.0,
                latency_ms=latency_ms,
                tokens=Tokens(**cached["tokens"]),
                request_id=request_id,
            )
            return _cached_envelope(cached["answer"], card)
        record_cache("miss")

    if forced:
        chosen, reason = forced, f"用户强制指定 {forced['name']}"
    else:
        try:
            chosen, reason = _select(PROVIDERS, messages, req, required_capabilities)
        except HTTPException:
            request.state.metric_outcome = "routing_error"
            raise

    if req.stream:
        return await _stream_response(
            req=req,
            request=request,
            request_id=request_id,
            payload=payload,
            messages=messages,
            chosen=chosen,
            reason=reason,
            required_capabilities=required_capabilities,
            started=started,
        )

    result, chosen, reason, tried_names = await _call_non_stream(
        req=req,
        payload=payload,
        messages=messages,
        chosen=chosen,
        reason=reason,
        required_capabilities=required_capabilities,
        request=request,
    )
    cost = estimate_cost(chosen, result.input_tokens, result.output_tokens)
    latency_ms = int((time.perf_counter() - started) * 1000)
    card = DecisionCard(
        chosen_provider=chosen["name"],
        reason=reason,
        cache_hit=False,
        cost_estimate_usd=cost,
        latency_ms=latency_ms,
        tokens=Tokens(input=result.input_tokens, output=result.output_tokens),
        request_id=request_id,
        retries=result.retries,
        failover_from=tried_names[0] if len(tried_names) > 1 else None,
    )
    record_usage(chosen["name"], result.input_tokens, result.output_tokens, cost)
    if key is not None and result.content and len(tried_names) == 1:
        CACHE.set(key, {"answer": result.content, "tokens": card.tokens.model_dump()})
    request.state.metric_outcome = "success"
    log.info(
        '{"request_id":"%s","event":"served","provider":"%s","cost":%s,"latency_ms":%s}',
        request_id,
        chosen["name"],
        cost,
        latency_ms,
    )
    return _provider_envelope(result.response, result.content, card)


async def _call_non_stream(
    *,
    req: GatewayRequest,
    payload: dict[str, Any],
    messages: list[dict],
    chosen: dict,
    reason: str,
    required_capabilities: set[str],
    request: Request,
):
    tried_names: list[str] = []
    while True:
        tried_names.append(chosen["name"])
        provider_started = time.perf_counter()
        try:
            result = await run_in_threadpool(
                call_provider_with_resilience,
                chosen,
                payload,
                BREAKER,
                provider_call=call_provider,
            )
            record_provider(chosen["name"], "success", time.perf_counter() - provider_started)
            return result, chosen, reason, tried_names
        except Exception as exc:
            record_provider(chosen["name"], "error", time.perf_counter() - provider_started)
            log.info(
                '{"event":"provider_error","provider":"%s","error_type":"%s"}',
                chosen["name"],
                type(exc).__name__,
            )
            remaining = [provider for provider in PROVIDERS if provider["name"] not in tried_names]
            if not remaining:
                request.state.metric_outcome = "provider_error"
                raise HTTPException(
                    status_code=502,
                    detail=f"所有 Provider 均不可用，最后失败: {type(exc).__name__}",
                ) from exc
            try:
                fallback, fallback_reason = _select(
                    remaining, messages, req, required_capabilities
                )
            except HTTPException as route_error:
                request.state.metric_outcome = "provider_error"
                raise HTTPException(
                    status_code=502,
                    detail=f"所有 Provider 均不可用，最后失败: {type(exc).__name__}",
                ) from route_error
            reason = (
                f"{reason}；{chosen['name']} 失败，自动切换到 "
                f"{fallback['name']}（{fallback_reason}）"
            )
            chosen = fallback


async def _stream_response(
    *,
    req: GatewayRequest,
    request: Request,
    request_id: str,
    payload: dict[str, Any],
    messages: list[dict],
    chosen: dict,
    reason: str,
    required_capabilities: set[str],
    started: float,
):
    tried_names: list[str] = []
    while True:
        tried_names.append(chosen["name"])
        provider_started = time.perf_counter()
        try:
            opened = await open_provider_stream_with_resilience(
                chosen, payload, BREAKER
            )
            break
        except Exception as exc:
            record_provider(chosen["name"], "error", time.perf_counter() - provider_started)
            log.info(
                '{"request_id":"%s","event":"stream_open_error","provider":"%s","error_type":"%s"}',
                request_id,
                chosen["name"],
                type(exc).__name__,
            )
            remaining = [provider for provider in PROVIDERS if provider["name"] not in tried_names]
            if not remaining:
                request.state.metric_outcome = "provider_error"
                raise HTTPException(
                    status_code=502,
                    detail=f"所有 Provider 均不可用，最后失败: {type(exc).__name__}",
                ) from exc
            try:
                fallback, fallback_reason = _select(
                    remaining, messages, req, required_capabilities
                )
            except HTTPException as route_error:
                request.state.metric_outcome = "provider_error"
                raise HTTPException(
                    status_code=502,
                    detail=f"所有 Provider 均不可用，最后失败: {type(exc).__name__}",
                ) from route_error
            reason = (
                f"{reason}；{chosen['name']} 失败，自动切换到 "
                f"{fallback['name']}（{fallback_reason}）"
            )
            chosen = fallback

    request.state.defer_metrics_to_stream = True
    observation = _StreamObservation()
    include_usage = (req.stream_options or {}).get("include_usage") is True

    async def downstream() -> AsyncIterator[bytes]:
        outcome = "partial_error"
        provider_outcome = "error"
        try:
            events = _with_first(opened.first_event, opened.remaining_events)
            async for event in events:
                if await request.is_disconnected():
                    outcome = "cancelled"
                    break
                observation.consume(event)
                if include_usage or not _is_usage_only_event(event):
                    yield event
            else:
                if observation.saw_done and observation.finish_reason is not None:
                    outcome = "success"
                    provider_outcome = "success"
        except asyncio.CancelledError:
            outcome = "cancelled"
            raise
        except Exception as exc:
            log.info(
                '{"request_id":"%s","event":"stream_error","provider":"%s","error_type":"%s"}',
                request_id,
                chosen["name"],
                type(exc).__name__,
            )
        finally:
            with contextlib.suppress(Exception):
                await opened.aclose()
            if outcome == "success":
                BREAKER.record_success(chosen["name"])
            elif outcome == "partial_error":
                BREAKER.record_failure(chosen["name"])
            else:
                BREAKER.record_cancelled(chosen["name"])
                provider_outcome = "cancelled"
            duration = time.perf_counter() - provider_started
            record_provider(chosen["name"], provider_outcome, duration)
            if outcome == "success":
                cost = estimate_cost(
                    chosen, observation.input_tokens, observation.output_tokens
                )
                record_usage(
                    chosen["name"],
                    observation.input_tokens,
                    observation.output_tokens,
                    cost,
                )
            record_request(outcome, time.perf_counter() - started)
            log.info(
                '{"request_id":"%s","event":"stream_finished","provider":"%s","outcome":"%s","reason":%s}',
                request_id,
                chosen["name"],
                outcome,
                json.dumps(reason, ensure_ascii=False),
            )

    return StreamingResponse(
        downstream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "X-Accel-Buffering": "no",
            "X-PolyGate-Request-ID": request_id,
            "X-PolyGate-Provider": chosen["name"],
        },
    )


async def _with_first(first: bytes, rest: AsyncIterator[bytes]) -> AsyncIterator[bytes]:
    yield first
    async for event in rest:
        yield event


class _StreamObservation:
    def __init__(self) -> None:
        self.input_tokens = 0
        self.output_tokens = 0
        self.finish_reason: str | None = None
        self.saw_done = False

    def consume(self, event: bytes) -> None:
        for data in _sse_data_values(event):
            if data == "[DONE]":
                self.saw_done = True
                continue
            try:
                chunk = json.loads(data)
            except json.JSONDecodeError as exc:
                raise ProviderProtocolError("provider returned malformed SSE data") from exc
            if not isinstance(chunk, dict) or chunk.get("error") is not None:
                raise ProviderProtocolError("provider returned an invalid SSE chunk")
            usage = chunk.get("usage") or {}
            self.input_tokens = int(usage.get("prompt_tokens", self.input_tokens))
            self.output_tokens = int(usage.get("completion_tokens", self.output_tokens))
            for choice in chunk.get("choices") or []:
                if choice.get("finish_reason") is not None:
                    self.finish_reason = str(choice["finish_reason"])


def _is_usage_only_event(event: bytes) -> bool:
    """Identify the optional usage chunk so it can be hidden when unrequested."""
    chunks: list[dict[str, Any]] = []
    for data in _sse_data_values(event):
        if not data or data == "[DONE]":
            return False
        try:
            chunk = json.loads(data)
        except json.JSONDecodeError:
            return False
        if not isinstance(chunk, dict):
            return False
        chunks.append(chunk)
    return bool(chunks) and all(
        not chunk.get("choices") and chunk.get("usage") is not None
        for chunk in chunks
    )


def _sse_data_values(event: bytes) -> list[str]:
    """Parse data fields from one or more complete SSE events."""
    values: list[str] = []
    data_lines: list[str] = []
    for raw_line in event.decode("utf-8", errors="replace").splitlines():
        if raw_line == "":
            if data_lines:
                values.append("\n".join(data_lines).strip())
                data_lines = []
            continue
        if raw_line.startswith("data:"):
            value = raw_line[5:]
            if value.startswith(" "):
                value = value[1:]
            data_lines.append(value)
    if data_lines:
        values.append("\n".join(data_lines).strip())
    return values


def _cached_envelope(answer: str, card: DecisionCard) -> dict:
    return {
        "id": card.request_id,
        "object": "chat.completion",
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": answer},
                "finish_reason": "stop",
            }
        ],
        "answer": answer,
        "polygate": card.model_dump(),
    }


def _provider_envelope(
    provider_response: dict[str, Any], answer: str, card: DecisionCard
) -> dict[str, Any]:
    envelope = dict(provider_response)
    envelope["answer"] = answer
    envelope["polygate"] = card.model_dump()
    return envelope
