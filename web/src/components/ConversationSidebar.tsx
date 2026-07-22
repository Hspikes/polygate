import { useState } from "react";
import type { Conversation } from "../domain/conversation";
import { useConversations } from "../store/ConversationProvider";
import { CloseIcon, MoreIcon, PlusIcon, SparkIcon } from "./icons";

interface ConversationSidebarProps {
  open: boolean;
  onClose: () => void;
}

const timestamp = new Intl.DateTimeFormat("zh-CN", { month: "numeric", day: "numeric" });

function ConversationItem({ conversation, active, onSelect }: {
  conversation: Conversation;
  active: boolean;
  onSelect: () => void;
}) {
  const { renameConversation, deleteConversation } = useConversations();
  const [editing, setEditing] = useState(false);
  const [title, setTitle] = useState(conversation.title);

  const save = () => {
    renameConversation(conversation.id, title);
    setEditing(false);
  };

  return (
    <li className={`conversation-item ${active ? "active" : ""}`}>
      {editing ? (
        <input
          className="conversation-title-input"
          value={title}
          autoFocus
          aria-label="会话名称"
          onChange={(event) => setTitle(event.target.value)}
          onBlur={save}
          onKeyDown={(event) => {
            if (event.key === "Enter") save();
            if (event.key === "Escape") {
              setTitle(conversation.title);
              setEditing(false);
            }
          }}
        />
      ) : (
        <button className="conversation-select" type="button" onClick={onSelect}>
          <span>{conversation.title}</span>
          <small>{timestamp.format(new Date(conversation.updatedAt))}</small>
        </button>
      )}
      <details className="conversation-menu">
        <summary aria-label={`管理 ${conversation.title}`}><MoreIcon /></summary>
        <div className="menu-popover">
          <button type="button" onClick={() => setEditing(true)}>重命名</button>
          <button className="danger" type="button" onClick={() => deleteConversation(conversation.id)}>删除</button>
        </div>
      </details>
    </li>
  );
}

export function ConversationSidebar({ open, onClose }: ConversationSidebarProps) {
  const { state, createConversation, selectConversation, clearAll } = useConversations();

  return (
    <>
      {open && <button className="sidebar-backdrop" type="button" aria-label="关闭会话栏" onClick={onClose} />}
      <aside className={`sidebar ${open ? "open" : ""}`} aria-label="会话列表">
        <div className="sidebar-brand">
          <SparkIcon />
          <span>PolyGate</span>
          <button className="icon-button sidebar-close" type="button" aria-label="关闭会话栏" onClick={onClose}>
            <CloseIcon />
          </button>
        </div>
        <button className="new-conversation" type="button" onClick={() => { createConversation(); onClose(); }}>
          <PlusIcon /> 新建会话
        </button>
        <nav className="conversation-nav">
          <p className="section-label">最近会话</p>
          <ul>
            {state.conversations.map((conversation) => (
              <ConversationItem
                key={conversation.id}
                conversation={conversation}
                active={conversation.id === state.activeConversationId}
                onSelect={() => { selectConversation(conversation.id); onClose(); }}
              />
            ))}
          </ul>
        </nav>
        <div className="sidebar-footer">
          <span>记录仅保存在此浏览器</span>
          <button
            type="button"
            onClick={() => {
              if (window.confirm("清空此浏览器中的全部 PolyGate 会话？")) clearAll();
            }}
          >
            清空全部
          </button>
        </div>
      </aside>
    </>
  );
}
