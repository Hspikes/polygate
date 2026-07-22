import { describe, expect, it, vi } from "vitest";
import {
  createConversation,
  DEFAULT_SETTINGS,
  type ChatMessage,
  type ConversationState,
  type DecisionCardData,
} from "../src/domain/conversation";
import { conversationReducer } from "../src/store/conversation-reducer";

const user = (id = "user-1", content = "first prompt"): ChatMessage => ({
  id,
  role: "user",
  content,
  createdAt: "2026-07-22T00:00:00.000Z",
  status: "complete",
});

const assistant = (id = "assistant-1", status: ChatMessage["status"] = "sending"): ChatMessage => ({
  id,
  role: "assistant",
  content: status === "complete" ? "answer" : "",
  createdAt: "2026-07-22T00:00:00.000Z",
  status,
  parentUserMessageId: "user-1",
  requestSettings: { ...DEFAULT_SETTINGS },
});

const card: DecisionCardData = {
  chosenProvider: "mock-a",
  reason: "test",
  cacheHit: false,
  costEstimateUsd: 0.0001,
  latencyMs: 20,
  tokens: { input: 4, output: 2 },
  retries: 0,
  failoverFrom: null,
  requestId: "req-test",
};

const stateWith = (...conversations: ReturnType<typeof createConversation>[]): ConversationState => ({
  conversations,
  activeConversationId: conversations[0]?.id ?? null,
  hydrated: true,
});

describe("conversationReducer", () => {
  it("creates, selects, renames and deletes conversations", () => {
    const first = createConversation({ title: "one" });
    const second = createConversation({ title: "two" });
    let state = conversationReducer(stateWith(first), { type: "CREATE_CONVERSATION", conversation: second });
    expect(state.activeConversationId).toBe(second.id);
    state = conversationReducer(state, { type: "RENAME_CONVERSATION", conversationId: second.id, title: "renamed" });
    expect(state.conversations[0].title).toBe("renamed");
    state = conversationReducer(state, { type: "SELECT_CONVERSATION", conversationId: first.id });
    state = conversationReducer(state, { type: "DELETE_CONVERSATION", conversationId: first.id });
    expect(state.activeConversationId).toBe(second.id);
  });

  it("appends a request pair and derives the first title", () => {
    const conversation = createConversation();
    const state = conversationReducer(stateWith(conversation), {
      type: "APPEND_USER_MESSAGE",
      conversationId: conversation.id,
      userMessage: user("u", "A long first prompt that names this conversation"),
      assistantMessage: assistant("a"),
    });
    expect(state.conversations[0].messages).toHaveLength(2);
    expect(state.conversations[0].title).toMatch(/^A long first prompt/);
  });

  it("retry changes only the assistant placeholder and never duplicates the user message", () => {
    const conversation = { ...createConversation(), messages: [user(), assistant("assistant-1", "error")] };
    const before = stateWith(conversation);
    const state = conversationReducer(before, {
      type: "RETRY_REQUEST",
      conversationId: conversation.id,
      messageId: "assistant-1",
      settings: { ...DEFAULT_SETTINGS, quality: "high" },
    });
    expect(state.conversations[0].messages).toHaveLength(2);
    expect(state.conversations[0].messages[0]).toEqual(user());
    expect(state.conversations[0].messages[1]).toMatchObject({ status: "sending", requestSettings: { quality: "high" } });
  });

  it("regeneration clears only the targeted assistant response", () => {
    const earlier = assistant("earlier", "complete");
    const target = assistant("target", "complete");
    const conversation = { ...createConversation(), messages: [user(), earlier, user("user-2"), target] };
    const state = conversationReducer(stateWith(conversation), {
      type: "REGENERATE_RESPONSE",
      conversationId: conversation.id,
      messageId: "target",
      settings: DEFAULT_SETTINGS,
    });
    expect(state.conversations[0].messages[1]).toEqual(earlier);
    expect(state.conversations[0].messages[3]).toMatchObject({ content: "", status: "sending" });
  });

  it("preserves per-message settings when later conversation preferences change", () => {
    const complete = { ...assistant("a", "complete"), decisionCard: card };
    const conversation = { ...createConversation(), messages: [user(), complete] };
    const state = conversationReducer(stateWith(conversation), {
      type: "UPDATE_SETTINGS",
      conversationId: conversation.id,
      settings: { maxCostUsd: 0.9 },
    });
    expect(state.conversations[0].settings.maxCostUsd).toBe(0.9);
    expect(state.conversations[0].messages[1].requestSettings?.maxCostUsd).toBe(DEFAULT_SETTINGS.maxCostUsd);
  });

  it("makes high-privacy conversations ephemeral atomically", () => {
    const conversation = createConversation();
    const state = conversationReducer(stateWith(conversation), {
      type: "UPDATE_SETTINGS",
      conversationId: conversation.id,
      settings: { privacy: "high" },
    });
    expect(state.conversations[0]).toMatchObject({ storageMode: "ephemeral", settings: { privacy: "high" } });
  });

  it("writes a late response back to its original conversation", () => {
    vi.setSystemTime(new Date("2026-07-22T00:00:10.000Z"));
    const first = { ...createConversation({ title: "first" }), messages: [user(), assistant()] };
    const second = createConversation({ title: "active" });
    const initial = { ...stateWith(first, second), activeConversationId: second.id };
    const state = conversationReducer(initial, {
      type: "COMPLETE_REQUEST",
      conversationId: first.id,
      messageId: "assistant-1",
      content: "late answer",
      decisionCard: card,
    });
    expect(state.activeConversationId).toBe(second.id);
    expect(state.conversations[0].messages[1].content).toBe("late answer");
    expect(state.conversations[1].messages).toHaveLength(0);
  });
});
