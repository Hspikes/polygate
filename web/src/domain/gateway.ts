import { z } from "zod";
import type { DecisionCardData, RoutingSettings } from "./conversation";

const tokensSchema = z.object({
  input: z.number().int().nonnegative(),
  output: z.number().int().nonnegative(),
});

export const decisionCardSchema = z.object({
  chosen_provider: z.string(),
  reason: z.string(),
  cache_hit: z.boolean(),
  cost_estimate_usd: z.number().nonnegative(),
  latency_ms: z.number().int().nonnegative(),
  tokens: tokensSchema,
  retries: z.number().int().nonnegative().default(0),
  failover_from: z.string().nullable().optional().default(null),
  request_id: z.string(),
});

export const gatewayResponseSchema = z
  .object({
    answer: z.string().optional(),
    choices: z
      .array(
        z.object({
          message: z.object({ role: z.literal("assistant"), content: z.string() }),
        }),
      )
      .optional(),
    polygate: decisionCardSchema,
  })
  .transform((data) => {
    const answer = data.answer ?? data.choices?.[0]?.message.content;
    if (answer === undefined) throw new Error("Gateway response did not contain an answer");
    const card = data.polygate;
    const decisionCard: DecisionCardData = {
      chosenProvider: card.chosen_provider,
      reason: card.reason,
      cacheHit: card.cache_hit,
      costEstimateUsd: card.cost_estimate_usd,
      latencyMs: card.latency_ms,
      tokens: card.tokens,
      retries: card.retries,
      failoverFrom: card.failover_from,
      requestId: card.request_id,
    };
    return { answer, decisionCard };
  });

export interface GatewayMessage {
  role: "system" | "user" | "assistant";
  content: string;
}

export interface GatewayRequest {
  model: string;
  messages: GatewayMessage[];
  polygate: {
    quality: RoutingSettings["quality"];
    privacy: RoutingSettings["privacy"];
    max_cost_usd: number;
    latency_target_ms: number;
  };
}
