import { z } from "zod";
import { DEFAULT_SETTINGS, type ConversationState } from "../domain/conversation";

export const STORAGE_KEY = "polygate.conversations";
export const SCHEMA_VERSION = 1;

const settingsSchema = z.object({
  model: z.string().default("auto"),
  quality: z.enum(["balanced", "high", "cheap"]),
  privacy: z.enum(["standard", "high"]),
  maxCostUsd: z.number().nonnegative(),
  latencyTargetMs: z.number().int().positive(),
});

const decisionCardSchema = z.object({
  chosenProvider: z.string(),
  reason: z.string(),
  cacheHit: z.boolean(),
  costEstimateUsd: z.number().nonnegative(),
  latencyMs: z.number().int().nonnegative(),
  tokens: z.object({ input: z.number().int().nonnegative(), output: z.number().int().nonnegative() }),
  retries: z.number().int().nonnegative(),
  failoverFrom: z.string().nullable(),
  requestId: z.string(),
});

const messageSchema = z.object({
  id: z.string(),
  role: z.enum(["system", "user", "assistant"]),
  content: z.string(),
  createdAt: z.string(),
  status: z.enum(["sending", "complete", "error", "cancelled"]),
  requestSettings: settingsSchema.optional(),
  decisionCard: decisionCardSchema.optional(),
  error: z
    .object({
      kind: z.enum([
        "auth",
        "network",
        "routing",
        "provider",
        "timeout",
        "budget",
        "rate_limit",
        "validation",
        "unknown",
      ]),
      message: z.string(),
      status: z.number().int().optional(),
      requestId: z.string().optional(),
    })
    .optional(),
  parentUserMessageId: z.string().optional(),
});

const conversationSchema = z.object({
  id: z.string(),
  title: z.string(),
  createdAt: z.string(),
  updatedAt: z.string(),
  settings: settingsSchema,
  messages: z.array(messageSchema),
  storageMode: z.literal("persistent"),
});

const persistedSchema = z.object({
  schemaVersion: z.literal(SCHEMA_VERSION),
  activeConversationId: z.string().nullable(),
  conversations: z.array(conversationSchema),
});

type UnknownRecord = Record<string, unknown>;

function migrate(raw: unknown): unknown {
  if (!raw || typeof raw !== "object") return raw;
  const record = raw as UnknownRecord;
  if (record.schemaVersion === SCHEMA_VERSION) return raw;
  if (record.schemaVersion === 0 && Array.isArray(record.conversations)) {
    return {
      ...record,
      schemaVersion: SCHEMA_VERSION,
      conversations: record.conversations.map((value) => {
        if (!value || typeof value !== "object") return value;
        const conversation = value as UnknownRecord;
        return {
          ...conversation,
          settings: { ...DEFAULT_SETTINGS, ...((conversation.settings as UnknownRecord | undefined) ?? {}) },
          storageMode: "persistent",
        };
      }),
    };
  }
  return raw;
}

export function restoreState(storage: Pick<Storage, "getItem"> = localStorage): ConversationState {
  try {
    const serialized = storage.getItem(STORAGE_KEY);
    if (!serialized) return { conversations: [], activeConversationId: null, hydrated: true };
    const parsed = persistedSchema.safeParse(migrate(JSON.parse(serialized)));
    if (!parsed.success) return { conversations: [], activeConversationId: null, hydrated: true };
    const conversations = parsed.data.conversations.map((conversation) => ({
      ...conversation,
      messages: conversation.messages.map((message) =>
        message.status === "sending" ? { ...message, status: "cancelled" as const } : message,
      ),
    }));
    const activeExists = conversations.some(({ id }) => id === parsed.data.activeConversationId);
    return {
      conversations,
      activeConversationId: activeExists
        ? parsed.data.activeConversationId
        : (conversations[0]?.id ?? null),
      hydrated: true,
    };
  } catch {
    return { conversations: [], activeConversationId: null, hydrated: true };
  }
}

export function persistState(
  state: ConversationState,
  storage: Pick<Storage, "setItem"> = localStorage,
): boolean {
  try {
    const conversations = state.conversations.filter(({ storageMode }) => storageMode === "persistent");
    const activeConversationId = conversations.some(({ id }) => id === state.activeConversationId)
      ? state.activeConversationId
      : (conversations[0]?.id ?? null);
    storage.setItem(
      STORAGE_KEY,
      JSON.stringify({ schemaVersion: SCHEMA_VERSION, activeConversationId, conversations }),
    );
    return true;
  } catch {
    return false;
  }
}

export function clearPersistedState(storage: Pick<Storage, "removeItem"> = localStorage): void {
  storage.removeItem(STORAGE_KEY);
}
