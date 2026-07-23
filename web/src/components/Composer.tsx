import { useEffect, useRef, useState } from "react";
import type { Conversation } from "../domain/conversation";
import { useConversations } from "../store/ConversationProvider";
import { SendIcon } from "./icons";
import { RoutingPanel } from "./RoutingPanel";

export function Composer({ conversation, draft, onDraftChange }: {
  conversation: Conversation;
  draft: string;
  onDraftChange: (draft: string) => void;
}) {
  const { sendMessage, cancelRequest, gatewayOnline, loadFixture } = useConversations();
  const [validation, setValidation] = useState("");
  const textarea = useRef<HTMLTextAreaElement>(null);
  const pending = conversation.messages.findLast(({ status }) => status === "sending");

  useEffect(() => {
    if (draft) textarea.current?.focus();
  }, [draft]);

  const submit = () => {
    if (!draft.trim()) {
      setValidation("请输入内容后再发送。");
      textarea.current?.focus();
      return;
    }
    if (draft.length > 8000) {
      setValidation("单条消息不能超过 8,000 个字符。");
      return;
    }
    const content = draft;
    onDraftChange("");
    setValidation("");
    void sendMessage(content);
  };

  return (
    <div className="composer-area">
      <RoutingPanel conversation={conversation} />
      <div className="composer">
        <textarea
          ref={textarea}
          rows={2}
          maxLength={8001}
          value={draft}
          placeholder={conversation.messages.length === 0 ? "向 PolyGate 提问…" : "继续对话…"}
          aria-label="消息内容"
          onChange={(event) => { onDraftChange(event.target.value); setValidation(""); }}
          onKeyDown={(event) => {
            if (event.key === "Enter" && !event.shiftKey) {
              event.preventDefault();
              if (!pending) submit();
            }
          }}
        />
        <div className="composer-toolbar">
          <div className="composer-secondary">
            {gatewayOnline === false && <button type="button" onClick={() => void loadFixture()}>加载离线演示</button>}
            {draft.length > 0 && <span>{draft.length.toLocaleString()} / 8,000</span>}
          </div>
          {pending ? (
            <button className="send-button cancel-button" type="button" onClick={() => cancelRequest(pending.id)}>取消生成</button>
          ) : (
            <button className="send-button" type="button" onClick={submit} disabled={!draft.trim()}>
              发送 <SendIcon />
            </button>
          )}
        </div>
        {validation && <p className="composer-validation" role="alert">{validation}</p>}
      </div>
    </div>
  );
}
