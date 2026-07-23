import { useEffect, useRef, useState } from "react";
import type { Conversation } from "../domain/conversation";
import { useConversations } from "../store/ConversationProvider";
import { CloseIcon, MoreIcon, PlusIcon, PolyGateMark, SidebarIcon } from "./icons";

interface ConversationSidebarProps {
  open: boolean;
  collapsed: boolean;
  onClose: () => void;
  onToggleCollapsed: () => void;
}

const calendarDate = new Intl.DateTimeFormat("zh-CN", { month: "numeric", day: "numeric" });

function timestamp(value: string): string {
  const date = new Date(value);
  const today = new Date();
  const dayStart = new Date(today.getFullYear(), today.getMonth(), today.getDate()).getTime();
  const dateStart = new Date(date.getFullYear(), date.getMonth(), date.getDate()).getTime();
  const daysAgo = Math.round((dayStart - dateStart) / 86_400_000);
  if (daysAgo === 0) return "今天";
  if (daysAgo === 1) return "昨天";
  return calendarDate.format(date);
}

function ConversationItem({ conversation, active, onSelect }: {
  conversation: Conversation;
  active: boolean;
  onSelect: () => void;
}) {
  const { renameConversation, deleteConversation } = useConversations();
  const [editing, setEditing] = useState(false);
  const [menuOpen, setMenuOpen] = useState(false);
  const [title, setTitle] = useState(conversation.title);
  const menu = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!menuOpen) return;
    const closeFromOutside = (event: PointerEvent) => {
      if (!menu.current?.contains(event.target as Node)) setMenuOpen(false);
    };
    const closeFromEscape = (event: KeyboardEvent) => {
      if (event.key === "Escape") setMenuOpen(false);
    };
    document.addEventListener("pointerdown", closeFromOutside);
    document.addEventListener("keydown", closeFromEscape);
    return () => {
      document.removeEventListener("pointerdown", closeFromOutside);
      document.removeEventListener("keydown", closeFromEscape);
    };
  }, [menuOpen]);

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
          <small>{timestamp(conversation.updatedAt)}</small>
        </button>
      )}
      <div ref={menu} className={`conversation-menu${menuOpen ? " open" : ""}`}>
        <button
          className="conversation-menu-trigger"
          type="button"
          aria-label={`管理 ${conversation.title}`}
          aria-haspopup="menu"
          aria-expanded={menuOpen}
          onClick={() => setMenuOpen((open) => !open)}
        ><MoreIcon /></button>
        {menuOpen && (
          <div className="menu-popover" role="menu">
            <button type="button" role="menuitem" onClick={() => { setMenuOpen(false); setEditing(true); }}>重命名</button>
            <button className="danger" type="button" role="menuitem" onClick={() => deleteConversation(conversation.id)}>删除</button>
          </div>
        )}
      </div>
    </li>
  );
}

export function ConversationSidebar({ open, collapsed, onClose, onToggleCollapsed }: ConversationSidebarProps) {
  const { state, createConversation, selectConversation, clearAll } = useConversations();

  return (
    <>
      {open && <button className="sidebar-backdrop" type="button" aria-label="关闭会话栏" onClick={onClose} />}
      <aside className={`sidebar ${open ? "open" : ""} ${collapsed ? "collapsed" : ""}`} aria-label="会话列表">
        <div className="sidebar-brand">
          <PolyGateMark className="polygate-mark" />
          <span>PolyGate</span>
          <button
            className="icon-button sidebar-toggle"
            type="button"
            aria-label={collapsed ? "展开会话栏" : "收起会话栏"}
            title={collapsed ? "展开会话栏" : "收起会话栏"}
            onClick={onToggleCollapsed}
          >
            <SidebarIcon />
          </button>
          <button className="icon-button sidebar-close" type="button" aria-label="关闭会话栏" onClick={onClose}>
            <CloseIcon />
          </button>
        </div>
        <button className="new-conversation" type="button" onClick={() => { createConversation(); onClose(); }}>
          <PlusIcon /> <span>新建会话</span>
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
