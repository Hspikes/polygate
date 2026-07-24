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

export const decisionRecordSchema = z
  .object({
    schema_version: z.literal(1),
    request_id: z.string().regex(/^req_[0-9a-f]{32}$/),
    outcome: z.enum(["success", "cache_hit", "cancelled", "partial_error"]),
    chosen_provider: z.string().min(1),
    initial_provider: z.string().min(1),
    reason: z.string().min(1),
    cache_hit: z.boolean(),
    stream: z.boolean(),
    cost_estimate_usd: z.number().nonnegative(),
    latency_ms: z.number().int().nonnegative(),
    tokens: tokensSchema,
    retries: z.number().int().nonnegative(),
    failover_from: z.string().nullable(),
    failover_count: z.number().int().nonnegative(),
    created_at: z.string(),
    expires_at: z.string(),
  })
  .transform((record): DecisionCardData => ({
    chosenProvider: record.chosen_provider,
    initialProvider: record.initial_provider,
    reason: record.reason,
    cacheHit: record.cache_hit,
    costEstimateUsd: record.cost_estimate_usd,
    latencyMs: record.latency_ms,
    tokens: record.tokens,
    retries: record.retries,
    failoverFrom: record.failover_from,
    failoverCount: record.failover_count,
    requestId: record.request_id,
    outcome: record.outcome,
    stream: record.stream,
    createdAt: record.created_at,
    expiresAt: record.expires_at,
  }));

export interface GatewayMessage {
  role: "system" | "user" | "assistant";
  content: string;
}

export interface GatewayRequest {
  model: string;
  messages: GatewayMessage[];
  stream?: boolean;
  stream_options?: { include_usage: boolean };
  polygate: {
    quality: RoutingSettings["quality"];
    privacy: RoutingSettings["privacy"];
    max_cost_usd: number;
    latency_target_ms: number;
  };
}
