import { describe, expect, it } from "vitest";
import { gatewayResponseSchema } from "../src/domain/gateway";

const valid = {
  answer: "hello",
  polygate: {
    chosen_provider: "mock-a",
    reason: "balanced",
    cache_hit: false,
    cost_estimate_usd: 0.0001,
    latency_ms: 10,
    tokens: { input: 2, output: 1 },
    request_id: "req-1",
  },
};

describe("gateway response contract", () => {
  it("maps wire-format decision card fields into client fields", () => {
    expect(gatewayResponseSchema.parse(valid)).toMatchObject({
      answer: "hello",
      decisionCard: { chosenProvider: "mock-a", retries: 0, failoverFrom: null },
    });
  });

  it("rejects incomplete decision cards", () => {
    expect(gatewayResponseSchema.safeParse({ answer: "hello", polygate: {} }).success).toBe(false);
  });
});
