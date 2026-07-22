#!/usr/bin/env bash
# Verify the public Web entry point, same-origin proxy and a multi-turn request.

set -euo pipefail

WEB_BASE="${WEB_BASE:-http://localhost:8080}"
SKIP_WEB_HEALTH="${SKIP_WEB_HEALTH:-0}"

python3 - "$WEB_BASE" "$SKIP_WEB_HEALTH" <<'PY'
import json
import sys
import time
import urllib.request

base = sys.argv[1].rstrip("/")
skip_web_health = sys.argv[2] == "1"

def get(path):
    with urllib.request.urlopen(base + path, timeout=8) as response:
        return response.status, response.read().decode("utf-8")

def post(messages):
    payload = json.dumps({
        "model": "auto",
        "messages": messages,
        "polygate": {
            "quality": "balanced",
            "privacy": "standard",
            "max_cost_usd": 0.01,
            "latency_target_ms": 3000,
        },
    }).encode("utf-8")
    request = urllib.request.Request(
        base + "/api/v1/chat/completions",
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.load(response)

if not skip_web_health:
    status, health = get("/healthz")
    assert status == 200 and health.strip() == "ok", (status, health)
    print("OK  Web health")

status, index = get("/")
assert status == 200 and '<div id="root"></div>' in index, "React entry document missing"
print("OK  React entry document")

nonce = time.time_ns()
first_messages = [{"role": "user", "content": f"web smoke first turn {nonce}"}]
first = post(first_messages)
answer = first.get("answer") or first["choices"][0]["message"]["content"]
assert answer and first.get("polygate", {}).get("request_id"), first
print("OK  Same-origin /api completion")

second = post([
    *first_messages,
    {"role": "assistant", "content": answer},
    {"role": "user", "content": "web smoke second turn"},
])
assert second.get("polygate", {}).get("request_id"), second
print("OK  Multi-turn completion through Web entry")
PY
