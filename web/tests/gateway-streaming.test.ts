import { describe, expect, it, vi } from "vitest";
import { requestStreamingCompletion } from "../src/api/gateway-client";
import type { GatewayRequest } from "../src/domain/gateway";

const requestId = "req_0123456789abcdef0123456789abcdef";

const payload: GatewayRequest = {
  model: "auto",
  messages: [{ role: "user", content: "hello" }],
  stream: true,
  stream_options: { include_usage: true },
  polygate: {
    quality: "balanced",
    privacy: "standard",
    max_cost_usd: 0.01,
    latency_target_ms: 3000,
  },
};

const decisionRecord = () => ({
  schema_version: 1,
  request_id: requestId,
  outcome: "success",
  chosen_provider: "mock-a",
  initial_provider: "mock-b",
  reason: "mock-b failed; selected mock-a",
  cache_hit: false,
  stream: true,
  cost_estimate_usd: 0.0001,
  latency_ms: 42,
  tokens: { input: 3, output: 2 },
  retries: 2,
  failover_from: "mock-b",
  failover_count: 1,
  created_at: "2026-07-24T03:00:00Z",
  expires_at: "2026-07-24T04:00:00Z",
});

function responseFromBytes(bytes: Uint8Array, splitEvery = 1): Response {
  const chunks: Uint8Array[] = [];
  for (let offset = 0; offset < bytes.length; offset += splitEvery) {
    chunks.push(bytes.slice(offset, offset + splitEvery));
  }
  return new Response(new ReadableStream<Uint8Array>({
    start(controller) {
      chunks.forEach((chunk) => controller.enqueue(chunk));
      controller.close();
    },
  }), {
    status: 200,
    headers: {
      "Content-Type": "text/event-stream; charset=utf-8",
      "X-PolyGate-Request-ID": requestId,
      "X-PolyGate-Provider": "mock-b",
    },
  });
}

describe("Gateway streaming client", () => {
  it("parses fragmented SSE and UTF-8 while ignoring supported tool-call chunks", async () => {
    const wire = [
      ': keepalive\r\n\r\n',
      'data: {"choices":[{"delta":{"tool_calls":[{"index":0}]},"finish_reason":null}]}\r\n\r\n',
      'data: {"choices":[{"delta":{"content":"你"},"finish_reason":null}]}\r\n\r\n',
      'data: {"choices":[\r\n',
      'data: {"delta":{"content":"好"},"finish_reason":null}\r\n',
      'data: ]}\r\n\r\n',
      'data: {"choices":[],"usage":{"prompt_tokens":3,"completion_tokens":2}}\r\n\r\n',
      'data: {"choices":[{"delta":{},"finish_reason":"stop"}]}\r\n\r\n',
      'data: [DONE]\r\n\r\n',
    ].join("");
    const deltas: string[] = [];
    const fetchMock = vi.fn(async (input: RequestInfo | URL, _init?: RequestInit) => {
      if (String(input) === "/api/v1/chat/completions") {
        return responseFromBytes(new TextEncoder().encode(wire));
      }
      if (String(input) === `/api/v1/decisions/${requestId}`) {
        return new Response(JSON.stringify(decisionRecord()), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        });
      }
      throw new Error(`unexpected fetch ${String(input)}`);
    });
    vi.stubGlobal("fetch", fetchMock);

    const result = await requestStreamingCompletion(
      payload,
      new AbortController().signal,
      (delta) => deltas.push(delta),
    );

    expect(deltas).toEqual(["你", "好"]);
    expect(result).toMatchObject({
      answer: "你好",
      decisionCard: {
        chosenProvider: "mock-a",
        initialProvider: "mock-b",
        retries: 2,
        failoverCount: 1,
        requestId,
      },
    });
    const chatInit = fetchMock.mock.calls[0][1] as RequestInit;
    expect(JSON.parse(String(chatInit.body))).toMatchObject({
      stream: true,
      stream_options: { include_usage: true },
    });
  });

  it("rejects an EOF without DONE as a partial response and keeps emitted text", async () => {
    const wire = 'data: {"choices":[{"delta":{"content":"partial"},"finish_reason":null}]}\n\n';
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(
      responseFromBytes(new TextEncoder().encode(wire), 7),
    ));
    const deltas: string[] = [];

    await expect(requestStreamingCompletion(
      payload,
      new AbortController().signal,
      (delta) => deltas.push(delta),
    )).rejects.toMatchObject({
      details: {
        kind: "partial",
        requestId,
      },
    });
    expect(deltas).toEqual(["partial"]);
  });

  it("propagates cancellation and cancels the response reader", async () => {
    const cancel = vi.fn();
    const controller = new AbortController();
    const response = new Response(new ReadableStream<Uint8Array>({
      start(streamController) {
        streamController.enqueue(new TextEncoder().encode(
          'data: {"choices":[{"delta":{"content":"partial"},"finish_reason":null}]}\n\n',
        ));
      },
      cancel,
    }), {
      status: 200,
      headers: {
        "Content-Type": "text/event-stream",
        "X-PolyGate-Request-ID": requestId,
      },
    });
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(response));

    const completion = requestStreamingCompletion(payload, controller.signal, () => {
      controller.abort();
    });

    await expect(completion).rejects.toMatchObject({ name: "AbortError" });
    expect(cancel).toHaveBeenCalled();
  });

  it("classifies a pre-stream 504 with its request ID", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(
      '{"error":{"message":"stream start budget exhausted"}}',
      {
        status: 504,
        headers: { "X-PolyGate-Request-ID": requestId },
      },
    )));

    await expect(requestStreamingCompletion(
      payload,
      new AbortController().signal,
      vi.fn(),
    )).rejects.toMatchObject({
      details: { kind: "timeout", status: 504, requestId },
    });
  });

  it("keeps a complete answer when the final Decision Record is unavailable", async () => {
    const wire = [
      'data: {"choices":[{"delta":{"content":"complete"},"finish_reason":"stop"}]}\n\n',
      'data: [DONE]\n\n',
    ].join("");
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => (
      String(input).includes("/decisions/")
        ? new Response('{"detail":"decision record store unavailable"}', { status: 503 })
        : responseFromBytes(new TextEncoder().encode(wire), 5)
    )));

    const result = await requestStreamingCompletion(
      payload,
      new AbortController().signal,
      vi.fn(),
    );
    expect(result).toMatchObject({
      answer: "complete",
      warning: {
        kind: "decision",
        requestId,
      },
    });
    expect(result.decisionCard).toBeUndefined();
  });

  it("briefly retries a Decision Record that is not visible yet", async () => {
    const wire = [
      'data: {"choices":[{"delta":{"content":"ready"},"finish_reason":"stop"}]}\n\n',
      'data: [DONE]\n\n',
    ].join("");
    let decisionAttempts = 0;
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => {
      if (!String(input).includes("/decisions/")) {
        return responseFromBytes(new TextEncoder().encode(wire), 3);
      }
      decisionAttempts += 1;
      return decisionAttempts === 1
        ? new Response('{"detail":"not found"}', { status: 404 })
        : new Response(JSON.stringify(decisionRecord()), {
            status: 200,
            headers: { "Content-Type": "application/json" },
          });
    }));

    const result = await requestStreamingCompletion(
      payload,
      new AbortController().signal,
      vi.fn(),
    );

    expect(decisionAttempts).toBe(2);
    expect(result.decisionCard?.requestId).toBe(requestId);
  });
});
