import { useState } from "react";
import type { ChatMessage } from "../domain/conversation";
import { useConversations } from "../store/ConversationProvider";
import { CopyIcon, SparkIcon } from "./icons";
import { DecisionCard } from "./DecisionCard";
import { MarkdownMessage } from "./MarkdownMessage";

const errorLabels = {
  network: "网络错误",
  routing: "无可用路由",
  provider: "Provider 错误",
  budget: "预算限制",
  validation: "请求不合法",
  unknown: "请求失败",
};

export function MessageBubble({ message }: { message: ChatMessage }) {
  const { cancelRequest, retryRequest } = useConversations();
  const [copied, setCopied] = useState(false);

  if (message.role === "user") {
    return (
      <article className="message-row user-message">
        <div className="message-bubble">{message.content}</div>
      </article>
    );
  }

  return (
    <article className="message-row assistant-message">
      <div className="assistant-avatar"><SparkIcon /></div>
      <div className="assistant-content">
        {message.status === "sending" && (
          <div className="request-state"><span className="spinner" />正在选择合适的模型并生成回答…</div>
        )}
        {message.status === "complete" && (
          <>
            <MarkdownMessage content={message.content} />
            <div className="message-actions">
              <button
                type="button"
                onClick={() => {
                  void navigator.clipboard.writeText(message.content).then(() => {
                    setCopied(true);
                    window.setTimeout(() => setCopied(false), 1400);
                  });
                }}
              ><CopyIcon /> {copied ? "已复制" : "复制回答"}</button>
            </div>
            {message.decisionCard && <DecisionCard card={message.decisionCard} settings={message.requestSettings} />}
          </>
        )}
        {message.status === "error" && (
          <div className={`request-error error-${message.error?.kind ?? "unknown"}`}>
            <strong>{errorLabels[message.error?.kind ?? "unknown"]}</strong>
            <span>{message.error?.message ?? "请求失败，请稍后重试。"}</span>
          </div>
        )}
        {message.status === "cancelled" && <div className="request-cancelled">这次生成已取消。</div>}
        {message.status === "sending" && (
          <button className="secondary-action" type="button" onClick={() => cancelRequest(message.id)}>取消</button>
        )}
        {(message.status === "error" || message.status === "cancelled") && (
          <button className="secondary-action" type="button" onClick={() => void retryRequest(message.id)}>重试</button>
        )}
      </div>
    </article>
  );
}
