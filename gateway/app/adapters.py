"""
Provider adapters: normalize each provider's response into the contract #5 shape.
Owned by A (interface) + B (real provider auth quirks).

Two modes:
  - FAKE_ADAPTER=1  -> returns canned responses WITHOUT any network call.
                       Lets A develop routing before B's mocks exist.
  - default         -> real HTTP call to the provider endpoint (mock or real).
"""
import os
import time
import httpx


class AdapterResult:
    def __init__(self, content: str, input_tokens: int, output_tokens: int, latency_ms: int):
        self.content = content
        self.input_tokens = input_tokens
        self.output_tokens = output_tokens
        self.latency_ms = latency_ms


def _fake_call(provider: dict, messages: list[dict]) -> AdapterResult:
    user_text = next((m["content"] for m in reversed(messages) if m["role"] == "user"), "")
    content = f"[fake:{provider['name']}] echo> {user_text[:80]}"
    return AdapterResult(content, input_tokens=max(1, len(user_text) // 4),
                         output_tokens=max(1, len(content) // 4), latency_ms=provider.get("typical_latency_ms", 200))


def call_provider(provider: dict, messages: list[dict], timeout_s: float = 10.0) -> AdapterResult:
    if os.environ.get("FAKE_ADAPTER") == "1":
        r = _fake_call(provider, messages)
        time.sleep(r.latency_ms / 1000.0 * 0.1)  # tiny sleep so latency numbers look real-ish
        return r

    headers = {"Content-Type": "application/json"}
    key_env = provider.get("api_key_env")
    if key_env and os.environ.get(key_env):
        headers["Authorization"] = f"Bearer {os.environ[key_env]}"

    body = {"model": provider.get("model", "default"), "messages": messages}
    started = time.perf_counter()
    resp = httpx.post(provider["endpoint"], json=body, headers=headers, timeout=timeout_s)
    resp.raise_for_status()
    data = resp.json()
    latency_ms = int((time.perf_counter() - started) * 1000)

    # Contract #5: OpenAI-shaped response WITH usage.
    content = data["choices"][0]["message"]["content"]
    usage = data.get("usage", {})
    return AdapterResult(
        content=content,
        input_tokens=int(usage.get("prompt_tokens", 0)),
        output_tokens=int(usage.get("completion_tokens", 0)),
        latency_ms=latency_ms,
    )
