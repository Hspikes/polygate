"""
Mock Provider — a controllable fake AI backend. Owned by Member B.

Serves an OpenAI-compatible /v1/chat/completions (contract #5, includes usage),
and exposes /admin/config (contract #3) so D's demo console can inject faults live.

Behaviour is controlled at runtime:
  POST /admin/config {"fail_rate": 1.0, "extra_latency_ms": 2000}
    fail_rate         probability [0..1] of returning HTTP 500
    extra_latency_ms  extra delay added to every response
"""
import os
import random
import time

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

NAME = os.environ.get("MOCK_NAME", "mock")
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


@app.post("/v1/chat/completions")
def chat_completions(body: dict):
    # inject configured latency
    if STATE["extra_latency_ms"]:
        time.sleep(STATE["extra_latency_ms"] / 1000.0)
    # inject configured failure
    if random.random() < STATE["fail_rate"]:
        raise HTTPException(status_code=500, detail=f"[{NAME}] injected failure")

    messages = body.get("messages", [])
    user_text = next((m["content"] for m in reversed(messages) if m.get("role") == "user"), "")
    content = f"Hello World from {NAME}! You said: {user_text[:120]}"

    prompt_tokens = max(1, sum(len(m.get("content", "")) for m in messages) // 4)
    completion_tokens = max(1, len(content) // 4)

    return {
        "id": f"chatcmpl-{NAME}-{random.randint(1000,9999)}",
        "object": "chat.completion",
        "choices": [{"index": 0, "message": {"role": "assistant", "content": content}, "finish_reason": "stop"}],
        "usage": {
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": prompt_tokens + completion_tokens,
        },
    }
