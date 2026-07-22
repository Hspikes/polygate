import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useReducer,
  useRef,
  useState,
  type PropsWithChildren,
} from "react";
import { GatewayClientError, gatewayHealth, requestCompletion } from "../api/gateway-client";
import {
  createConversation,
  newId,
  type ChatMessage,
  type Conversation,
  type ConversationState,
  type RoutingSettings,
  type StorageMode,
} from "../domain/conversation";
import { gatewayResponseSchema, type GatewayMessage, type GatewayRequest } from "../domain/gateway";
import { clearPersistedState, persistState, restoreState } from "../storage/local-conversations";
import { conversationReducer } from "./conversation-reducer";

interface ConversationContextValue {
  state: ConversationState;
  activeConversation: Conversation | null;
  gatewayOnline: boolean | null;
  createConversation: () => void;
  selectConversation: (conversationId: string) => void;
  renameConversation: (conversationId: string, title: string) => void;
  deleteConversation: (conversationId: string) => void;
  updateSettings: (settings: Partial<RoutingSettings>) => void;
  setStorageMode: (storageMode: StorageMode) => void;
  clearAll: () => void;
  sendMessage: (content: string) => Promise<void>;
  cancelRequest: (messageId: string) => void;
  retryRequest: (messageId: string) => Promise<void>;
  regenerateLast: () => Promise<void>;
  loadFixture: () => Promise<void>;
}

const ConversationContext = createContext<ConversationContextValue | null>(null);

function messagesForGateway(messages: ChatMessage[]): GatewayMessage[] {
  return messages
    .filter((message) => message.status === "complete" && message.content.trim())
    .map(({ role, content }) => ({ role, content }));
}

function gatewayPayload(settings: RoutingSettings, messages: ChatMessage[]): GatewayRequest {
  return {
    model: settings.model,
    messages: messagesForGateway(messages),
    polygate: {
      quality: settings.quality,
      privacy: settings.privacy,
      max_cost_usd: settings.maxCostUsd,
      latency_target_ms: settings.latencyTargetMs,
    },
  };
}

export function ConversationProvider({ children }: PropsWithChildren) {
  const [state, dispatch] = useReducer(conversationReducer, undefined, () => restoreState());
  const stateRef = useRef(state);
  const controllers = useRef(new Map<string, AbortController>());
  const [gatewayOnline, setGatewayOnline] = useState<boolean | null>(null);

  useEffect(() => {
    stateRef.current = state;
  }, [state]);

  useEffect(() => {
    if (state.hydrated) persistState(state);
  }, [state]);

  useEffect(() => {
    if (state.hydrated && state.conversations.length === 0) {
      dispatch({ type: "CREATE_CONVERSATION", conversation: createConversation() });
    }
  }, [state.hydrated, state.conversations.length]);

  useEffect(() => {
    const controller = new AbortController();
    void gatewayHealth(controller.signal).then(setGatewayOnline);
    const interval = window.setInterval(() => void gatewayHealth().then(setGatewayOnline), 30_000);
    return () => {
      controller.abort();
      window.clearInterval(interval);
    };
  }, []);

  useEffect(
    () => () => {
      controllers.current.forEach((controller) => controller.abort());
    },
    [],
  );

  const activeConversation = useMemo(
    () => state.conversations.find(({ id }) => id === state.activeConversationId) ?? null,
    [state.activeConversationId, state.conversations],
  );

  const runRequest = useCallback(
    async (
      conversationId: string,
      assistantMessageId: string,
      settings: RoutingSettings,
      requestMessages: ChatMessage[],
    ) => {
      const controller = new AbortController();
      controllers.current.set(assistantMessageId, controller);
      try {
        const response = await requestCompletion(gatewayPayload(settings, requestMessages), controller.signal);
        dispatch({
          type: "COMPLETE_REQUEST",
          conversationId,
          messageId: assistantMessageId,
          content: response.answer,
          decisionCard: response.decisionCard,
        });
        setGatewayOnline(true);
      } catch (error) {
        if (error instanceof DOMException && error.name === "AbortError") {
          dispatch({ type: "CANCEL_REQUEST", conversationId, messageId: assistantMessageId });
          return;
        }
        dispatch({
          type: "FAIL_REQUEST",
          conversationId,
          messageId: assistantMessageId,
          error:
            error instanceof GatewayClientError
              ? error.details
              : { kind: "unknown", message: "请求失败，请稍后重试。" },
        });
        if (error instanceof GatewayClientError && error.details.kind === "network") setGatewayOnline(false);
      } finally {
        controllers.current.delete(assistantMessageId);
      }
    },
    [],
  );

  const sendMessage = useCallback(
    async (content: string) => {
      const conversation = stateRef.current.conversations.find(
        ({ id }) => id === stateRef.current.activeConversationId,
      );
      const prompt = content.trim();
      if (!conversation || !prompt) return;

      const now = new Date().toISOString();
      const userMessage: ChatMessage = {
        id: newId("msg"),
        role: "user",
        content: prompt,
        createdAt: now,
        status: "complete",
      };
      const assistantMessage: ChatMessage = {
        id: newId("msg"),
        role: "assistant",
        content: "",
        createdAt: now,
        status: "sending",
        requestSettings: { ...conversation.settings },
        parentUserMessageId: userMessage.id,
      };
      dispatch({
        type: "APPEND_USER_MESSAGE",
        conversationId: conversation.id,
        userMessage,
        assistantMessage,
      });
      await runRequest(
        conversation.id,
        assistantMessage.id,
        conversation.settings,
        [...conversation.messages, userMessage],
      );
    },
    [runRequest],
  );

  const retryRequest = useCallback(
    async (messageId: string) => {
      const conversation = stateRef.current.conversations.find(({ messages }) =>
        messages.some(({ id }) => id === messageId),
      );
      if (!conversation) return;
      const index = conversation.messages.findIndex(({ id }) => id === messageId);
      const message = conversation.messages[index];
      if (message.role !== "assistant" || !["error", "cancelled"].includes(message.status)) return;
      const settings = { ...conversation.settings };
      dispatch({ type: "RETRY_REQUEST", conversationId: conversation.id, messageId, settings });
      await runRequest(conversation.id, messageId, settings, conversation.messages.slice(0, index));
    },
    [runRequest],
  );

  const regenerateLast = useCallback(async () => {
    const conversation = stateRef.current.conversations.find(
      ({ id }) => id === stateRef.current.activeConversationId,
    );
    if (!conversation) return;
    const index = conversation.messages.findLastIndex(({ role }) => role === "assistant");
    if (
      index < 0
      || conversation.messages[index].status !== "complete"
      || controllers.current.has(conversation.messages[index].id)
    ) return;
    const messageId = conversation.messages[index].id;
    const settings = { ...conversation.settings };
    dispatch({ type: "REGENERATE_RESPONSE", conversationId: conversation.id, messageId, settings });
    await runRequest(conversation.id, messageId, settings, conversation.messages.slice(0, index));
  }, [runRequest]);

  const cancelRequest = useCallback((messageId: string) => {
    controllers.current.get(messageId)?.abort();
  }, []);

  const loadFixture = useCallback(async () => {
    const conversation = stateRef.current.conversations.find(
      ({ id }) => id === stateRef.current.activeConversationId,
    );
    if (!conversation) return;
    try {
      const raw = await fetch("/fixtures/decision-card.example.json").then((response) => response.json());
      const fixture = gatewayResponseSchema.parse(raw);
      const now = new Date().toISOString();
      const userMessage: ChatMessage = {
        id: newId("msg"),
        role: "user",
        content: "展示一条 PolyGate 离线演示回答",
        createdAt: now,
        status: "complete",
      };
      const assistantMessage: ChatMessage = {
        id: newId("msg"),
        role: "assistant",
        content: fixture.answer,
        createdAt: now,
        status: "complete",
        requestSettings: { ...conversation.settings },
        parentUserMessageId: userMessage.id,
        decisionCard: fixture.decisionCard,
      };
      dispatch({
        type: "APPEND_USER_MESSAGE",
        conversationId: conversation.id,
        userMessage,
        assistantMessage,
      });
    } catch {
      // The fixture is only a fallback demo; a failure must not affect real requests.
    }
  }, []);

  const value = useMemo<ConversationContextValue>(
    () => ({
      state,
      activeConversation,
      gatewayOnline,
      createConversation: () => dispatch({ type: "CREATE_CONVERSATION", conversation: createConversation() }),
      selectConversation: (conversationId) => dispatch({ type: "SELECT_CONVERSATION", conversationId }),
      renameConversation: (conversationId, title) =>
        dispatch({ type: "RENAME_CONVERSATION", conversationId, title }),
      deleteConversation: (conversationId) => {
        state.conversations
          .find(({ id }) => id === conversationId)
          ?.messages.forEach(({ id }) => controllers.current.get(id)?.abort());
        dispatch({ type: "DELETE_CONVERSATION", conversationId });
      },
      updateSettings: (settings) => {
        if (activeConversation) {
          dispatch({ type: "UPDATE_SETTINGS", conversationId: activeConversation.id, settings });
        }
      },
      setStorageMode: (storageMode) => {
        if (activeConversation) {
          dispatch({ type: "SET_STORAGE_MODE", conversationId: activeConversation.id, storageMode });
        }
      },
      clearAll: () => {
        controllers.current.forEach((controller) => controller.abort());
        clearPersistedState();
        dispatch({ type: "CLEAR_ALL_CONVERSATIONS" });
      },
      sendMessage,
      cancelRequest,
      retryRequest,
      regenerateLast,
      loadFixture,
    }),
    [activeConversation, cancelRequest, gatewayOnline, loadFixture, regenerateLast, retryRequest, sendMessage, state],
  );

  return <ConversationContext.Provider value={value}>{children}</ConversationContext.Provider>;
}

export function useConversations(): ConversationContextValue {
  const context = useContext(ConversationContext);
  if (!context) throw new Error("useConversations must be used within ConversationProvider");
  return context;
}
