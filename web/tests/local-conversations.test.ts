import { describe, expect, it } from "vitest";
import { createConversation } from "../src/domain/conversation";
import { persistState, restoreState, SCHEMA_VERSION, STORAGE_KEY } from "../src/storage/local-conversations";

class MemoryStorage {
  value: string | null = null;
  getItem() { return this.value; }
  setItem(_key: string, value: string) { this.value = value; }
  removeItem() { this.value = null; }
}

describe("local conversation storage", () => {
  it("safely falls back when JSON is corrupt", () => {
    const storage = new MemoryStorage();
    storage.value = "not-json";
    expect(restoreState(storage)).toEqual({ conversations: [], activeConversationId: null, hydrated: true });
  });

  it("does not persist ephemeral conversations", () => {
    const storage = new MemoryStorage();
    const persistent = createConversation({ title: "keep" });
    const ephemeral = createConversation({ title: "drop", storageMode: "ephemeral" });
    persistState({ conversations: [persistent, ephemeral], activeConversationId: ephemeral.id, hydrated: true }, storage);
    const serialized = JSON.parse(storage.value ?? "{}") as { activeConversationId: string; conversations: unknown[] };
    expect(serialized.conversations).toHaveLength(1);
    expect(serialized.activeConversationId).toBe(persistent.id);
  });

  it("migrates schema version zero with missing settings", () => {
    const storage = new MemoryStorage();
    const conversation = createConversation();
    storage.value = JSON.stringify({
      schemaVersion: 0,
      activeConversationId: conversation.id,
      conversations: [{ ...conversation, storageMode: undefined, settings: { quality: "cheap" } }],
    });
    const restored = restoreState(storage);
    expect(restored.conversations[0].settings).toMatchObject({ quality: "cheap", privacy: "standard" });
    expect(restored.conversations[0].storageMode).toBe("persistent");
  });

  it("stores an explicit versioned envelope", () => {
    const storage = new MemoryStorage();
    const conversation = createConversation();
    expect(persistState({ conversations: [conversation], activeConversationId: conversation.id, hydrated: true }, storage)).toBe(true);
    expect(JSON.parse(storage.value ?? "{}")).toMatchObject({ schemaVersion: SCHEMA_VERSION });
    expect(STORAGE_KEY).toBe("polygate.conversations");
  });

  it("restores interrupted requests as cancelled so they can be retried", () => {
    const storage = new MemoryStorage();
    const conversation = createConversation();
    conversation.messages.push({
      id: "assistant-pending",
      role: "assistant",
      content: "",
      createdAt: conversation.createdAt,
      status: "sending",
    });
    persistState({ conversations: [conversation], activeConversationId: conversation.id, hydrated: true }, storage);
    expect(restoreState(storage).conversations[0].messages[0].status).toBe("cancelled");
  });
});
