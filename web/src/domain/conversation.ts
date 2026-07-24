export type MessageRole = "system" | "user" | "assistant";
export type MessageStatus = "sending" | "complete" | "error" | "cancelled";
export type StorageMode = "persistent" | "ephemeral";
export type Quality = "balanced" | "high" | "cheap";
export type Privacy = "standard" | "high";

export interface RoutingSettings {
  model: string;
  quality: Quality;
  privacy: Privacy;
  maxCostUsd: number;
  latencyTargetMs: number;
}

export interface TokenUsage {
  input: number;
  output: number;
}

export interface DecisionCardData {
  chosenProvider: string;
  reason: string;
  cacheHit: boolean;
  costEstimateUsd: number;
  latencyMs: number;
  tokens: TokenUsage;
  retries: number;
  failoverFrom: string | null;
  requestId: string;
}

export interface MessageError {
  kind:
    | "auth"
    | "network"
    | "routing"
    | "provider"
    | "timeout"
    | "budget"
    | "rate_limit"
    | "validation"
    | "unknown";
  message: string;
  status?: number;
  requestId?: string;
}

export interface ChatMessage {
  id: string;
  role: MessageRole;
  content: string;
  createdAt: string;
  status: MessageStatus;
  requestSettings?: RoutingSettings;
  decisionCard?: DecisionCardData;
  error?: MessageError;
  parentUserMessageId?: string;
}

export interface Conversation {
  id: string;
  title: string;
  createdAt: string;
  updatedAt: string;
  settings: RoutingSettings;
  messages: ChatMessage[];
  storageMode: StorageMode;
}

export interface ConversationState {
  conversations: Conversation[];
  activeConversationId: string | null;
  hydrated: boolean;
}

export const DEFAULT_SETTINGS: RoutingSettings = {
  model: "auto",
  quality: "balanced",
  privacy: "standard",
  maxCostUsd: 0.002,
  latencyTargetMs: 3000,
};

export const emptyState = (): ConversationState => ({
  conversations: [],
  activeConversationId: null,
  hydrated: false,
});

export const newId = (prefix: string): string =>
  `${prefix}_${globalThis.crypto?.randomUUID?.() ?? Math.random().toString(36).slice(2)}`;

export function createConversation(
  overrides: Partial<Pick<Conversation, "title" | "settings" | "storageMode">> = {},
): Conversation {
  const now = new Date().toISOString();
  return {
    id: newId("conv"),
    title: overrides.title ?? "新会话",
    createdAt: now,
    updatedAt: now,
    settings: { ...DEFAULT_SETTINGS, ...overrides.settings },
    messages: [],
    storageMode: overrides.storageMode ?? "persistent",
  };
}

export function titleFromPrompt(prompt: string): string {
  const oneLine = prompt.replace(/\s+/g, " ").trim();
  return oneLine.length > 28 ? `${oneLine.slice(0, 28)}…` : oneLine || "新会话";
}

export function estimateTokens(messages: ChatMessage[]): number {
  const chars = messages.reduce((total, message) => total + message.content.length, 0);
  return Math.ceil(chars / 4) + messages.length * 4;
}
