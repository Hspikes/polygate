import { useEffect, useRef } from "react";
import type { Conversation } from "../domain/conversation";
import { MessageBubble } from "./MessageBubble";
import { PolyGateMark } from "./icons";

export function MessageList({ conversation, onTryPrompt }: {
  conversation: Conversation;
  onTryPrompt: (prompt: string) => void;
}) {
  const bottomRef = useRef<HTMLDivElement>(null);
  useEffect(() => {
    void bottomRef.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [conversation.messages]);

  if (conversation.messages.length === 0) {
    return (
      <div className="empty-chat">
        <PolyGateMark className="polygate-mark" />
        <p className="eyebrow">智能模型路由</p>
        <h1>自由提问，聪明路由。</h1>
        <p>PolyGate 会在质量、隐私、成本与速度之间，为每一轮对话寻找合适路径。</p>
        <div className="prompt-suggestions">
          <button type="button" onClick={() => onTryPrompt("用三个要点解释云原生网关的价值。")}>解释一个概念</button>
          <button type="button" onClick={() => onTryPrompt("帮我起草一份项目进度更新。")}>起草一份更新</button>
          <button type="button" onClick={() => onTryPrompt("比较 Kubernetes Deployment 和 StatefulSet。")}>比较两个方案</button>
        </div>
      </div>
    );
  }

  const lastAssistant = conversation.messages.findLast(({ role }) => role === "assistant");

  return (
    <div className="message-list" aria-live="polite">
      {conversation.messages.map((message) => (
        <MessageBubble
          key={message.id}
          message={message}
          canRegenerate={message.id === lastAssistant?.id && message.status === "complete"}
        />
      ))}
      <div ref={bottomRef} />
    </div>
  );
}
