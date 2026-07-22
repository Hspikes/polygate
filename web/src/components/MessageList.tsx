import { useEffect, useRef } from "react";
import type { Conversation } from "../domain/conversation";
import { MessageBubble } from "./MessageBubble";
import { SparkIcon } from "./icons";

export function MessageList({ conversation, onTryPrompt }: {
  conversation: Conversation;
  onTryPrompt: (prompt: string) => void;
}) {
  const bottomRef = useRef<HTMLDivElement>(null);
  useEffect(() => bottomRef.current?.scrollIntoView({ behavior: "smooth", block: "end" }), [conversation.messages]);

  if (conversation.messages.length === 0) {
    return (
      <div className="empty-chat">
        <SparkIcon />
        <p className="eyebrow">INTELLIGENT MODEL ROUTING</p>
        <h1>Ask freely. Route wisely.</h1>
        <p>PolyGate 会在质量、隐私、成本与速度之间，为每一轮对话寻找合适路径。</p>
        <div className="prompt-suggestions">
          <button type="button" onClick={() => onTryPrompt("用三个要点解释云原生网关的价值。")}>解释一个概念</button>
          <button type="button" onClick={() => onTryPrompt("帮我起草一份项目进度更新。")}>起草一份更新</button>
          <button type="button" onClick={() => onTryPrompt("比较 Kubernetes Deployment 和 StatefulSet。")}>比较两个方案</button>
        </div>
      </div>
    );
  }

  return (
    <div className="message-list" aria-live="polite">
      {conversation.messages.map((message) => <MessageBubble key={message.id} message={message} />)}
      <div ref={bottomRef} />
    </div>
  );
}
