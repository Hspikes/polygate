import {
  titleFromPrompt,
  type ChatMessage,
  type Conversation,
  type ConversationState,
  type DecisionCardData,
  type MessageError,
  type RoutingSettings,
  type StorageMode,
} from "../domain/conversation";

export type ConversationAction =
  | { type: "CREATE_CONVERSATION"; conversation: Conversation }
  | { type: "SELECT_CONVERSATION"; conversationId: string }
  | { type: "RENAME_CONVERSATION"; conversationId: string; title: string }
  | { type: "DELETE_CONVERSATION"; conversationId: string }
  | { type: "UPDATE_SETTINGS"; conversationId: string; settings: Partial<RoutingSettings> }
  | { type: "SET_STORAGE_MODE"; conversationId: string; storageMode: StorageMode }
  | {
      type: "APPEND_USER_MESSAGE";
      conversationId: string;
      userMessage: ChatMessage;
      assistantMessage: ChatMessage;
    }
  | { type: "START_REQUEST"; conversationId: string; messageId: string; settings: RoutingSettings }
  | {
      type: "COMPLETE_REQUEST";
      conversationId: string;
      messageId: string;
      content: string;
      decisionCard: DecisionCardData;
    }
  | { type: "FAIL_REQUEST"; conversationId: string; messageId: string; error: MessageError }
  | { type: "CANCEL_REQUEST"; conversationId: string; messageId: string }
  | { type: "RETRY_REQUEST"; conversationId: string; messageId: string; settings: RoutingSettings }
  | { type: "REGENERATE_RESPONSE"; conversationId: string; messageId: string; settings: RoutingSettings }
  | { type: "RESTORE_PERSISTED_STATE"; state: ConversationState }
  | { type: "CLEAR_ALL_CONVERSATIONS" };

const withConversation = (
  state: ConversationState,
  conversationId: string,
  update: (conversation: Conversation) => Conversation,
): ConversationState => ({
  ...state,
  conversations: state.conversations.map((conversation) =>
    conversation.id === conversationId ? update(conversation) : conversation,
  ),
});

const updateMessage = (
  conversation: Conversation,
  messageId: string,
  update: (message: ChatMessage) => ChatMessage,
): Conversation => ({
  ...conversation,
  updatedAt: new Date().toISOString(),
  messages: conversation.messages.map((message) => (message.id === messageId ? update(message) : message)),
});

export function conversationReducer(
  state: ConversationState,
  action: ConversationAction,
): ConversationState {
  switch (action.type) {
    case "CREATE_CONVERSATION":
      return {
        ...state,
        conversations: [action.conversation, ...state.conversations],
        activeConversationId: action.conversation.id,
      };
    case "SELECT_CONVERSATION":
      return state.conversations.some(({ id }) => id === action.conversationId)
        ? { ...state, activeConversationId: action.conversationId }
        : state;
    case "RENAME_CONVERSATION": {
      const title = action.title.trim();
      if (!title) return state;
      return withConversation(state, action.conversationId, (conversation) => ({
        ...conversation,
        title,
        updatedAt: new Date().toISOString(),
      }));
    }
    case "DELETE_CONVERSATION": {
      const conversations = state.conversations.filter(({ id }) => id !== action.conversationId);
      const activeConversationId =
        state.activeConversationId === action.conversationId
          ? (conversations[0]?.id ?? null)
          : state.activeConversationId;
      return { ...state, conversations, activeConversationId };
    }
    case "UPDATE_SETTINGS":
      return withConversation(state, action.conversationId, (conversation) => ({
        ...conversation,
        settings: { ...conversation.settings, ...action.settings },
        storageMode: action.settings.privacy === "high" ? "ephemeral" : conversation.storageMode,
        updatedAt: new Date().toISOString(),
      }));
    case "SET_STORAGE_MODE":
      return withConversation(state, action.conversationId, (conversation) => ({
        ...conversation,
        storageMode: action.storageMode,
        updatedAt: new Date().toISOString(),
      }));
    case "APPEND_USER_MESSAGE":
      return withConversation(state, action.conversationId, (conversation) => ({
        ...conversation,
        title: conversation.messages.length === 0 ? titleFromPrompt(action.userMessage.content) : conversation.title,
        updatedAt: action.userMessage.createdAt,
        messages: [...conversation.messages, action.userMessage, action.assistantMessage],
      }));
    case "START_REQUEST":
    case "RETRY_REQUEST":
    case "REGENERATE_RESPONSE":
      return withConversation(state, action.conversationId, (conversation) =>
        updateMessage(conversation, action.messageId, (message) => ({
          ...message,
          content: "",
          status: "sending",
          requestSettings: { ...action.settings },
          decisionCard: undefined,
          error: undefined,
        })),
      );
    case "COMPLETE_REQUEST":
      return withConversation(state, action.conversationId, (conversation) =>
        updateMessage(conversation, action.messageId, (message) => ({
          ...message,
          content: action.content,
          status: "complete",
          decisionCard: action.decisionCard,
          error: undefined,
        })),
      );
    case "FAIL_REQUEST":
      return withConversation(state, action.conversationId, (conversation) =>
        updateMessage(conversation, action.messageId, (message) => ({
          ...message,
          status: "error",
          error: action.error,
        })),
      );
    case "CANCEL_REQUEST":
      return withConversation(state, action.conversationId, (conversation) =>
        updateMessage(conversation, action.messageId, (message) => ({
          ...message,
          status: "cancelled",
          error: undefined,
        })),
      );
    case "RESTORE_PERSISTED_STATE":
      return { ...action.state, hydrated: true };
    case "CLEAR_ALL_CONVERSATIONS":
      return { conversations: [], activeConversationId: null, hydrated: true };
    default:
      return state;
  }
}
