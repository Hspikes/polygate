import { useMemo, useState } from "react";
import { estimateTokens } from "../domain/conversation";
import { formatUsd } from "../domain/routing-display";
import { useConversations } from "../store/ConversationProvider";
import { Composer } from "./Composer";
import { ConversationSidebar } from "./ConversationSidebar";
import { MenuIcon, PlusIcon } from "./icons";
import { MessageList } from "./MessageList";

const CONTEXT_WARNING_TOKENS = 12_000;
const CONTEXT_LIMIT_ESTIMATE = 16_000;

export function ChatShell() {
  const { activeConversation, gatewayOnline, createConversation } = useConversations();
  const [sidebarOpen, setSidebarOpen] = useState(false);
  const [sidebarCollapsed, setSidebarCollapsed] = useState(false);
  const [draft, setDraft] = useState("");

  const stats = useMemo(() => {
    const cards = activeConversation?.messages.flatMap((message) => message.decisionCard ?? []) ?? [];
    const providers = cards.filter(({ chosenProvider }) => chosenProvider !== "cache").map(({ chosenProvider }) => chosenProvider);
    let switches = 0;
    providers.forEach((provider, index) => {
      if (index > 0 && provider !== providers[index - 1]) switches += 1;
    });
    return {
      cost: cards.reduce((sum, card) => sum + card.costEstimateUsd, 0),
      tokens: cards.reduce((sum, card) => sum + card.tokens.input + card.tokens.output, 0),
      cacheHits: cards.filter(({ cacheHit }) => cacheHit).length,
      requests: cards.length,
      switches,
    };
  }, [activeConversation]);

  if (!activeConversation) return <div className="app-loading">正在准备 PolyGate…</div>;

  const estimatedContext = estimateTokens(activeConversation.messages);
  return (
    <div className="app-shell">
      <ConversationSidebar
        open={sidebarOpen}
        collapsed={sidebarCollapsed}
        onClose={() => setSidebarOpen(false)}
        onToggleCollapsed={() => setSidebarCollapsed((collapsed) => !collapsed)}
      />
      <main className="chat-shell">
        <header className="chat-header">
          <button className="icon-button mobile-menu" type="button" aria-label="打开会话栏" onClick={() => setSidebarOpen(true)}><MenuIcon /></button>
          <div className="header-title">
            <h2>{activeConversation.title}</h2>
            <span className={`storage-pill ${activeConversation.storageMode}`}>{activeConversation.storageMode === "persistent" ? "本地保存" : "不保存"}</span>
          </div>
          {stats.requests > 0 && (
            <dl className="conversation-stats">
              <div><dt>累计成本</dt><dd>{formatUsd(stats.cost)}</dd></div>
              <div><dt>总 Token</dt><dd>{stats.tokens.toLocaleString()}</dd></div>
              <div><dt>缓存命中</dt><dd>{stats.cacheHits}<span> / {stats.requests}</span></dd></div>
              <div><dt>模型切换</dt><dd>{stats.switches}</dd></div>
            </dl>
          )}
          <span className={`gateway-status ${gatewayOnline === true ? "online" : gatewayOnline === false ? "offline" : "checking"}`}>
            <i /> {gatewayOnline === true ? "网关在线" : gatewayOnline === false ? "网关离线" : "检查中"}
          </span>
        </header>
        {estimatedContext >= CONTEXT_WARNING_TOKENS && (
          <div className="context-warning" role="status">
            <span>上下文估算 {estimatedContext.toLocaleString()} / {CONTEXT_LIMIT_ESTIMATE.toLocaleString()} Token，已接近保守上限；PolyGate 不会静默截断历史。</span>
            <button type="button" onClick={createConversation}><PlusIcon /> 新建会话</button>
          </div>
        )}
        <section className="chat-body">
          <MessageList conversation={activeConversation} onTryPrompt={setDraft} />
        </section>
        <Composer conversation={activeConversation} draft={draft} onDraftChange={setDraft} />
      </main>
    </div>
  );
}
