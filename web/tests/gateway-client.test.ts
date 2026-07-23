import { describe, expect, it, vi } from "vitest";
import {
  GatewayClientError,
  gatewayHealth,
  requestCompletion,
} from "../src/api/gateway-client";
import type { GatewayRequest } from "../src/domain/gateway";

const payload: GatewayRequest = {
  model: "auto",
  messages: [{ role: "user", content: "hello" }],
  polygate: {
    quality: "balanced",
    privacy: "standard",
    max_cost_usd: 0.01,
    latency_target_ms: 3000,
  },
};

const successBody = {
  answer: "hello",
  polygate: {
    chosen_provider: "mock-a",
    reason: "test",
    cache_hit: false,
    cost_estimate_usd: 0.0001,
    latency_ms: 10,
    tokens: { input: 2, output: 1 },
    request_id: "req-body",
  },
};

const signal = () => new AbortController().signal;

describe("Gateway client error contract", () => {
  it("classifies 401 and preserves the response request ID", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(
      '{"detail":"invalid or missing PolyGate API key"}',
      {
        status: 401,
        headers: { "X-PolyGate-Request-ID": "req-auth" },
      },
    )));

    await expect(requestCompletion(payload, signal())).rejects.toMatchObject({
      details: {
        kind: "auth",
        status: 401,
        requestId: "req-auth",
        message: "Web 凭证未配置或已失效，请联系管理员检查服务端配置。",
      },
    } satisfies Partial<GatewayClientError>);
  });

  it("reads the nested Gateway validation error message", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(JSON.stringify({
      error: {
        message: "invalid Chat Completions request",
        code: "request_validation_error",
        details: [],
      },
    }), { status: 422 })));

    await expect(requestCompletion(payload, signal())).rejects.toMatchObject({
      details: {
        kind: "validation",
        status: 422,
        message: "请求内容未通过校验：invalid Chat Completions request",
      },
    });
  });

  it("classifies a 504 as a retryable Provider timeout", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(
      '{"detail":"Provider 调用超过网关时间预算"}',
      {
        status: 504,
        headers: { "X-PolyGate-Request-ID": "req-timeout" },
      },
    )));

    await expect(requestCompletion(payload, signal())).rejects.toMatchObject({
      details: {
        kind: "timeout",
        status: 504,
        requestId: "req-timeout",
        message: "Provider 调用或流式启动超过时间预算，请重试。",
      },
    });
  });

  it.each([
    [403, "privacy=high rejected external provider", "validation"],
    [429, "request cost exceeds budget", "budget"],
    [429, "too many requests", "rate_limit"],
    [502, "provider unavailable", "provider"],
    [503, "no route satisfies constraints", "routing"],
  ] as const)("maps HTTP %i with detail %s to %s", async (status, detail, kind) => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(
      JSON.stringify({ detail }),
      { status },
    )));

    await expect(requestCompletion(payload, signal())).rejects.toMatchObject({
      details: { kind, status },
    });
  });

  it("turns transport failures into a stable network error", async () => {
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new TypeError("offline")));

    await expect(requestCompletion(payload, signal())).rejects.toMatchObject({
      details: {
        kind: "network",
        message: "无法连接网关，请检查服务和网络后重试。",
      },
    });
  });

  it("uses the response-header request ID on a successful decision card", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(
      JSON.stringify(successBody),
      {
        status: 200,
        headers: {
          "Content-Type": "application/json",
          "X-PolyGate-Request-ID": "req-header",
        },
      },
    )));

    await expect(requestCompletion(payload, signal())).resolves.toMatchObject({
      decisionCard: { requestId: "req-header" },
    });
  });

  it("preserves the request ID when a successful response is malformed", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(
      "not-json",
      {
        status: 200,
        headers: { "X-PolyGate-Request-ID": "req-malformed" },
      },
    )));

    await expect(requestCompletion(payload, signal())).rejects.toMatchObject({
      details: {
        kind: "validation",
        status: 200,
        requestId: "req-malformed",
        message: "网关响应不是有效的 JSON。",
      },
    });
  });

  it("checks the authenticated models endpoint for readiness", async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response("{}", { status: 401 }));
    vi.stubGlobal("fetch", fetchMock);

    await expect(gatewayHealth(signal())).resolves.toBe(false);
    expect(fetchMock).toHaveBeenCalledWith("/api/v1/models", expect.any(Object));
  });
});
