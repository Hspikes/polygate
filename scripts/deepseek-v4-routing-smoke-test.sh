#!/usr/bin/env bash
# Live Flash/Pro routing gate. This intentionally makes small paid API calls.

set -euo pipefail

GATEWAY_BASE="${GATEWAY_BASE:-http://127.0.0.1:8000}"
WEB_BASE="${WEB_BASE:-http://127.0.0.1:8080}"
AUTOMATION_BASE="${AUTOMATION_BASE:-http://127.0.0.1:8020}"

if [ -z "${POLYGATE_API_KEY:-}" ]; then
  echo "Set POLYGATE_API_KEY to a Gateway client key." >&2
  exit 1
fi

python3 - "$GATEWAY_BASE" "$WEB_BASE" "$AUTOMATION_BASE" <<'PY'
import json
import os
import sys
import time
import urllib.error
import urllib.request


gateway_base, web_base, automation_base = (value.rstrip("/") for value in sys.argv[1:])
gateway_key = os.environ["POLYGATE_API_KEY"]


def request_json(url, *, payload=None, headers=None, timeout=45):
    request = urllib.request.Request(
        url,
        data=None if payload is None else json.dumps(payload).encode(),
        headers=headers or {},
        method="GET" if payload is None else "POST",
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.status, response.headers, json.load(response)


def chat_case(label, model, *, budget=0.01, privacy="standard"):
    status, _, body = request_json(
        gateway_base + "/v1/chat/completions",
        payload={
            "model": model,
            "messages": [
                {
                    "role": "user",
                    "content": f"Reply exactly polygate-ok. case={label}-{time.time_ns()}",
                }
            ],
            "max_tokens": 32,
            "polygate": {
                "quality": "high",
                "privacy": privacy,
                "max_cost_usd": budget,
                "latency_target_ms": 3000,
                "cache_control": "no-store",
            },
        },
        headers={
            "Authorization": f"Bearer {gateway_key}",
            "Content-Type": "application/json",
        },
    )
    card = body.get("polygate") or {}
    choices = body.get("choices") or []
    content = ((choices[0].get("message") or {}).get("content") if choices else None)
    assert status == 200, (label, status, body)
    assert content, (label, "empty content", body)
    result = {
        "case": label,
        "provider": card.get("chosen_provider"),
        "model": body.get("model"),
        "rank_reason": "quality_rank=" in card.get("reason", ""),
    }
    print("OK ", json.dumps(result, ensure_ascii=False))
    return result


forced_flash = chat_case("forced-flash", "deepseek-flash")
forced_pro = chat_case("forced-pro", "deepseek-pro")
auto_pro = chat_case("auto-high", "auto")
budget_flash = chat_case("auto-high-tight-budget", "auto", budget=0.0001)
private = chat_case("auto-high-private", "auto", privacy="high")

assert (forced_flash["provider"], forced_flash["model"]) == (
    "deepseek-flash",
    "deepseek-v4-flash",
)
assert (forced_pro["provider"], forced_pro["model"]) == (
    "deepseek-pro",
    "deepseek-v4-pro",
)
assert (auto_pro["provider"], auto_pro["model"], auto_pro["rank_reason"]) == (
    "deepseek-pro",
    "deepseek-v4-pro",
    True,
)
assert (budget_flash["provider"], budget_flash["model"], budget_flash["rank_reason"]) == (
    "deepseek-flash",
    "deepseek-v4-flash",
    True,
)
assert private["provider"] in {"mock-a", "mock-b"}, private


stream_request = urllib.request.Request(
    web_base + "/api/v1/chat/completions",
    data=json.dumps(
        {
            "model": "auto",
            "messages": [
                {
                    "role": "user",
                    "content": f"Reply exactly polygate-stream-ok {time.time_ns()}",
                }
            ],
            "stream": True,
            "stream_options": {"include_usage": True},
            "max_tokens": 32,
            "polygate": {
                "quality": "high",
                "privacy": "standard",
                "max_cost_usd": 0.01,
                "latency_target_ms": 3000,
                "cache_control": "no-store",
            },
        }
    ).encode(),
    headers={"Content-Type": "application/json"},
    method="POST",
)
with urllib.request.urlopen(stream_request, timeout=45) as response:
    provider = response.headers.get("X-PolyGate-Provider")
    request_id = response.headers.get("X-PolyGate-Request-ID")
    content_parts = []
    usage = None
    saw_done = False
    for raw_line in response:
        line = raw_line.decode().strip()
        if not line.startswith("data:"):
            continue
        data = line[5:].strip()
        if data == "[DONE]":
            saw_done = True
            continue
        event = json.loads(data)
        usage = event.get("usage") or usage
        for choice in event.get("choices") or []:
            content = (choice.get("delta") or {}).get("content")
            if content:
                content_parts.append(content)

assert provider == "deepseek-pro", provider
assert request_id, request_id
assert "".join(content_parts).strip(), content_parts
assert saw_done
assert usage and usage.get("completion_tokens", 0) > 0, usage

for attempt in range(20):
    try:
        status, _, decision = request_json(
            web_base + "/api/v1/decisions/" + request_id,
            timeout=5,
        )
        break
    except urllib.error.HTTPError as error:
        if error.code != 404 or attempt == 19:
            raise
        time.sleep(0.1)
assert status == 200
assert decision["chosen_provider"] == "deepseek-pro", decision
assert decision["outcome"] == "success", decision
print("OK  Web SSE -> deepseek-pro -> Decision Record")


policy_admin_key = os.environ.get("POLICY_ADMIN_KEY")
if policy_admin_key:
    _, _, active = request_json(automation_base + "/v1/policies/active", timeout=5)
    _, _, preview = request_json(
        automation_base + "/v1/admin/policies/preview",
        payload={
            "policy": active["policy"],
            "gateway_cases": [
                {
                    "model": "auto",
                    "messages": [{"role": "user", "content": "Preview DeepSeek Pro"}],
                    "polygate": {
                        "quality": "high",
                        "privacy": "standard",
                        "max_cost_usd": 0.01,
                        "latency_target_ms": 3000,
                    },
                }
            ],
        },
        headers={
            "Authorization": f"Bearer {policy_admin_key}",
            "Content-Type": "application/json",
        },
        timeout=15,
    )
    routing = preview["simulations"]["routing"][0]
    assert routing["before"]["provider"] == "deepseek-pro", routing
    assert routing["after"]["provider"] == "deepseek-pro", routing
    assert "quality_rank=2" in routing["after"]["reason"], routing
    print("OK  Policy Editor preview -> deepseek-pro")
else:
    print("SKIP Policy Editor preview (POLICY_ADMIN_KEY is not set)")

print("DeepSeek V4 live routing smoke passed.")
PY
