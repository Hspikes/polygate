#!/usr/bin/env bash
# Verify the public Web entry point, same-origin proxy and a multi-turn request.

set -euo pipefail

WEB_BASE="${WEB_BASE:-http://localhost:8080}"
SKIP_WEB_HEALTH="${SKIP_WEB_HEALTH:-0}"

python3 - "$WEB_BASE" "$SKIP_WEB_HEALTH" <<'PY'
import json
import sys
import time
import urllib.error
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
        "stream": True,
        "stream_options": {"include_usage": True},
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
        request_id = response.headers.get("X-PolyGate-Request-ID")
        assert request_id, "stream response missing X-PolyGate-Request-ID"
        answer = []
        saw_done = False
        for raw_line in response:
            line = raw_line.decode("utf-8").strip()
            if not line.startswith("data:"):
                continue
            data = line[5:].strip()
            if data == "[DONE]":
                saw_done = True
                continue
            chunk = json.loads(data)
            for choice in chunk.get("choices", []):
                content = choice.get("delta", {}).get("content")
                if isinstance(content, str):
                    answer.append(content)
        assert saw_done, "stream ended without data: [DONE]"
        return "".join(answer), request_id

def decision(request_id):
    for attempt in range(10):
        try:
            status, body = get("/api/v1/decisions/" + request_id)
            assert status == 200, (status, body)
            record = json.loads(body)
            assert record.get("request_id") == request_id, record
            return record
        except urllib.error.HTTPError as error:
            if error.code != 404 or attempt == 9:
                raise
            time.sleep(0.1)
    raise AssertionError("Decision Record did not become available")

if not skip_web_health:
    status, health = get("/healthz")
    assert status == 200 and health.strip() == "ok", (status, health)
    print("OK  Web health")

status, models = get("/api/v1/models")
assert status == 200 and json.loads(models).get("object") == "list", (status, models)
print("OK  Authenticated Gateway readiness")

status, index = get("/")
assert status == 200 and '<div id="root"></div>' in index, "React entry document missing"
print("OK  React entry document")

nonce = time.time_ns()
first_messages = [{"role": "user", "content": f"web smoke first turn {nonce}"}]
answer, first_request_id = post(first_messages)
assert answer, answer
assert decision(first_request_id).get("outcome") == "success"
print("OK  Same-origin /api SSE completion + Decision Record")

second_answer, second_request_id = post([
    *first_messages,
    {"role": "assistant", "content": answer},
    {"role": "user", "content": "web smoke second turn"},
])
assert second_answer, second_answer
assert decision(second_request_id).get("outcome") == "success"
print("OK  Multi-turn SSE completion through Web entry")
PY
