"""
PolyGate Gateway — FastAPI entry. OpenAI-compatible /v1/chat/completions.
Owned by Member A. Ties together registry + router + adapters + cache + cost + decision card.

Run locally (no mocks needed):   FAKE_ADAPTER=1 uvicorn app.main:app --reload
Run against mocks:               uvicorn app.main:app --reload   (with mock-a/mock-b up)
"""
import logging
import os
import time
import uuid

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from prometheus_client import CONTENT_TYPE_LATEST

from app.models import GatewayRequest, DecisionCard, Tokens
from app.registry import load_providers
from app.router import select_provider
from app.adapters import call_provider
from app.cache import Cache, cache_key
from app.cost import estimate_cost
from app.metrics import (
    record_cache,
    record_provider,
    record_request,
    record_usage,
    render_metrics,
)

logging.basicConfig(level=logging.INFO, format='{"ts":"%(asctime)s","lvl":"%(levelname)s","msg":%(message)s}')
log = logging.getLogger("polygate")

app = FastAPI(title="PolyGate Gateway", version="0.1.0-p0")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

PROVIDERS = load_providers()
CACHE = Cache()
# P0: static health map; B replaces this with live probes in P1.
HEALTH: dict[str, str] = {}


@app.middleware("http")
async def record_chat_request(request: Request, call_next):
    """Record every chat request exactly once, including validation errors."""
    if (
        request.method != "POST"
        or request.url.path != "/v1/chat/completions"
    ):
        return await call_next(request)

    started = time.perf_counter()
    try:
        response = await call_next(request)
    except Exception:
        record_request("server_error", time.perf_counter() - started)
        raise

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
    return {"status": "ok", "providers": [p["name"] for p in PROVIDERS], "cache": CACHE.enabled}


@app.get("/metrics", include_in_schema=False)
def metrics():
    """Prometheus scrape endpoint for gateway business and process metrics."""
    return Response(content=render_metrics(), media_type=CONTENT_TYPE_LATEST)


@app.get("/providers")
def providers():
    # D's dashboard reads this to show price/latency/health per provider.
    return [
        {
            "name": p["name"], "kind": p.get("kind"), "privacy": p.get("privacy"),
            "price_per_1k_input": p.get("price_per_1k_input"),
            "price_per_1k_output": p.get("price_per_1k_output"),
            "typical_latency_ms": p.get("typical_latency_ms"),
            "health": HEALTH.get(p["name"], "healthy"),
        }
        for p in PROVIDERS
    ]


@app.post("/v1/chat/completions")
def chat_completions(req: GatewayRequest, request: Request):
    request_id = "req_" + uuid.uuid4().hex[:8]
    c = req.polygate
    messages = [m.model_dump() for m in req.messages]
    t0 = time.perf_counter()

    # 0. 先解析 forced provider，并在查缓存之前完成存在性 + 隐私校验
    #    这一步必须在 cache lookup 之前，否则未知/越权的强制指定会被缓存直接放行（见 code review @V）
    forced = None
    if req.model != "auto":
        forced = next((p for p in PROVIDERS if p["name"] == req.model), None)
        if forced is None:
            raise HTTPException(status_code=400, detail=f"未知的 provider: {req.model}")
        if c.privacy == "high" and forced.get("privacy") == "external":
            raise HTTPException(
                status_code=403,
                detail=f"privacy=high 禁止强制路由到外部 Provider {forced['name']}"
            )

    # 1. cache lookup —— key 里带上 forced provider 的身份（cache_scope），
    #    以及 quality/max_cost_usd（自动路由时这两个约束会影响实际选中的 provider），
    #    避免"强制指定 mock-b"被"mock-a 的历史缓存"张冠李戴，
    #    也避免"改约束但 messages 没变"时被缓存直接短路、观察不到路由结果变化
    cache_scope = forced["name"] if forced else "auto"
    key = cache_key(messages, c.privacy, cache_scope, c.quality, c.max_cost_usd)
    cached = CACHE.get(key)
    if cached:
        record_cache("hit")
        request.state.metric_outcome = "cache_hit"
        latency_ms = int((time.perf_counter() - t0) * 1000)
        card = DecisionCard(
            chosen_provider="cache", reason="精确缓存命中，未调用任何 Provider，成本为 0",
            cache_hit=True, cost_estimate_usd=0.0, latency_ms=latency_ms,
            tokens=Tokens(**cached["tokens"]), request_id=request_id,
        )
        log.info(f'{{"request_id":"{request_id}","event":"cache_hit"}}')
        return _envelope(cached["answer"], card)
    record_cache("miss")

    # 2. route（forced 已在上面解析并校验过，这里直接使用）
    try:
        if forced:
            chosen, reason = forced, f"用户强制指定 {forced['name']}"
        else:
            chosen, reason, _ = select_provider(PROVIDERS, messages, c, HEALTH)
    except RuntimeError as e:
        request.state.metric_outcome = "routing_error"
        raise HTTPException(status_code=503, detail=str(e))

    # 3. call provider via adapter
    provider_t0 = time.perf_counter()
    try:
        result = call_provider(chosen, messages)
    except Exception as e:
        record_provider(chosen["name"], "error", time.perf_counter() - provider_t0)
        request.state.metric_outcome = "provider_error"
        # P0: surface the error. P1 (Member B) adds retry + circuit breaker + failover here.
        log.info(f'{{"request_id":"{request_id}","event":"provider_error","provider":"{chosen["name"]}","err":"{e}"}}')
        raise HTTPException(status_code=502, detail=f"provider {chosen['name']} failed: {e}")
    record_provider(chosen["name"], "success", time.perf_counter() - provider_t0)

    # 4. cost + decision card
    cost = estimate_cost(chosen, result.input_tokens, result.output_tokens)
    latency_ms = int((time.perf_counter() - t0) * 1000)
    card = DecisionCard(
        chosen_provider=chosen["name"], reason=reason, cache_hit=False,
        cost_estimate_usd=cost, latency_ms=latency_ms,
        tokens=Tokens(input=result.input_tokens, output=result.output_tokens),
        request_id=request_id,
    )
    record_usage(
        provider=chosen["name"],
        input_tokens=result.input_tokens,
        output_tokens=result.output_tokens,
        estimated_cost_usd=cost,
    )

    # 5. store in cache for next identical request —— key 用同一个 cache_scope/quality/max_cost_usd，保证查/存一致
    CACHE.set(key, {"answer": result.content, "tokens": card.tokens.model_dump()})
    request.state.metric_outcome = "success"
    log.info(f'{{"request_id":"{request_id}","event":"served","provider":"{chosen["name"]}","cost":{cost},"latency_ms":{latency_ms}}}')
    return _envelope(result.content, card)


def _envelope(answer: str, card: DecisionCard) -> dict:
    """OpenAI-shaped response + polygate decision card."""
    return {
        "id": card.request_id,
        "object": "chat.completion",
        "choices": [{"index": 0, "message": {"role": "assistant", "content": answer}, "finish_reason": "stop"}],
        "answer": answer,  # convenience mirror for D's simple UI
        "polygate": card.model_dump(),
    }
