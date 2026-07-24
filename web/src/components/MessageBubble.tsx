import { useState } from "react";
import type { ChatMessage } from "../domain/conversation";
import { useConversations } from "../store/ConversationProvider";
import { CopyIcon, PolyGateMark, RefreshIcon } from "./icons";
import { DecisionCard } from "./DecisionCard";
import { MarkdownMessage } from "./MarkdownMessage";

const errorLabels = {
  auth: "认证错误",
  network: "网络错误",
  routing: "无可用路由",
  provider: "模型服务错误",
  timeout: "模型响应超时",
  budget: "预算限制",
  rate_limit: "请求过于频繁",
  validation: "请求不合法",
  partial: "回答不完整",
  decision: "路由记录不可用",
  unknown: "请求失败",
};

export function MessageBubble({ message, canRegenerate = false }: { message: ChatMessage; canRegenerate?: boolean }) {
  const { regenerateLast, retryRequest } = useConversations();
  const [copied, setCopied] = useState(false);
  const [requestIdCopied, setRequestIdCopied] = useState(false);

  if (message.role === "user") {
    return (
      <article className="message-row user-message">
        <div className="message-bubble">{message.content}</div>
      </article>
    );
  }

  const hasContent = message.content.length > 0;
  const responseCard = hasContent ? (
    <section
      className={`response-card${canRegenerate ? " has-regenerate" : ""}`}
      aria-label="助手回复"
    >
      <div className="response-actions">
        <button
          className="message-action"
          type="button"
          data-copied={copied}
          aria-label={copied ? "回答已复制" : "复制回答"}
          title={copied ? "已复制" : "复制回答"}
          onClick={() => {
            void navigator.clipboard.writeText(message.content).then(() => {
              setCopied(true);
              window.setTimeout(() => setCopied(false), 1400);
            });
          }}
        ><CopyIcon /><span>{copied ? "已复制" : "复制"}</span></button>
        {canRegenerate && (
          <button
            className="message-action"
            type="button"
            aria-label="重新生成回答"
            title="重新生成回答"
            onClick={() => void regenerateLast()}
          ><RefreshIcon /></button>
        )}
      </div>
      <div className="response-card-body">
        <MarkdownMessage content={message.content} />
      </div>
    </section>
  ) : null;

  return (
    <article className="message-row assistant-message">
      <div className="assistant-avatar"><PolyGateMark className="polygate-mark" /></div>
      <div className="assistant-content">
        {message.status === "sending" && !hasContent && (
          <div className="request-feedback request-progress" role="status">
            <span className="request-feedback-copy">
              <strong>正在生成回答</strong>
              <span>评估质量、成本与延迟，选择合适模型…</span>
            </span>
            <span className="progress-sheen" aria-hidden="true" />
          </div>
        )}
        {message.status === "sending" && hasContent && (
          <>
            {responseCard}
            <div className="request-feedback request-progress" role="status">
              <span className="request-feedback-copy">
                <strong>正在生成回答</strong>
                <span>已开始接收模型输出…</span>
              </span>
              <span className="progress-sheen" aria-hidden="true" />
            </div>
          </>
        )}
        {message.status === "complete" && (
          <>
            {responseCard}
            {message.decisionCard && <DecisionCard card={message.decisionCard} settings={message.requestSettings} />}
            {message.warning && (
              <div className="request-feedback request-error error-decision" role="status">
                <span className="request-feedback-copy">
                  <strong>{errorLabels[message.warning.kind]}</strong>
                  <span>{message.warning.message}</span>
                  {message.warning.requestId && (
                    <span className="request-error-trace">
                      Request ID: <code>{message.warning.requestId}</code>
                      <button
                        type="button"
                        aria-label="复制 request ID"
                        title={requestIdCopied ? "Request ID 已复制" : "复制 request ID"}
                        onClick={() => {
                          void navigator.clipboard.writeText(message.warning!.requestId!).then(() => {
                            setRequestIdCopied(true);
                            window.setTimeout(() => setRequestIdCopied(false), 1400);
                          });
                        }}
                      >{requestIdCopied ? "已复制" : "复制"}</button>
                    </span>
                  )}
                </span>
              </div>
            )}
          </>
        )}
        {message.status === "error" && (
          <>
            {responseCard}
            <div className={`request-feedback request-error error-${message.error?.kind ?? "unknown"}`} role="alert">
              <span className="request-feedback-copy">
                <strong>{errorLabels[message.error?.kind ?? "unknown"]}</strong>
                <span>{message.error?.message ?? "请求失败，请稍后重试。"}</span>
                {message.error?.requestId && (
                  <span className="request-error-trace">
                    Request ID: <code>{message.error.requestId}</code>
                    <button
                      type="button"
                      aria-label="复制 request ID"
                      title={requestIdCopied ? "Request ID 已复制" : "复制 request ID"}
                      onClick={() => {
                        void navigator.clipboard.writeText(message.error!.requestId!).then(() => {
                          setRequestIdCopied(true);
                          window.setTimeout(() => setRequestIdCopied(false), 1400);
                        });
                      }}
                    >{requestIdCopied ? "已复制" : "复制"}</button>
                  </span>
                )}
              </span>
              <button className="request-control" type="button" onClick={() => void retryRequest(message.id)}>重试</button>
            </div>
          </>
        )}
        {message.status === "cancelled" && (
          <>
            {responseCard}
            <div className="request-feedback request-cancelled">
              <span className="request-feedback-copy">
                <strong>生成已停止</strong>
                <span>已接收的内容会保留，可以随时重新生成。</span>
              </span>
              <button className="request-control" type="button" onClick={() => void retryRequest(message.id)}>重试</button>
            </div>
          </>
        )}
      </div>
    </article>
  );
}
