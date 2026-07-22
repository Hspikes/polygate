import { useMemo, useState } from "react";
import { estimateTokens } from "../domain/conversation";
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
      switches,
    };
  }, [activeConversation]);

  if (!activeConversation) return <div className="app-loading">正在准备 PolyGate…</div>;

  const estimatedContext = estimateTokens(activeConversation.messages);
  return (
    <div className="app-shell">
      <ConversationSidebar open={sidebarOpen} onClose={() => setSidebarOpen(false)} />
      <main className="chat-shell">
        <header className="chat-header">
          <button className="icon-button mobile-menu" type="button" aria-label="打开会话栏" onClick={() => setSidebarOpen(true)}><MenuIcon /></button>
          <div className="header-title">
            <h2>{activeConversation.title}</h2>
            <span className={`storage-pill ${activeConversation.storageMode}`}>{activeConversation.storageMode === "persistent" ? "本地保存" : "不保存"}</span>
          </div>
          <dl className="conversation-stats">
            <div><dt>累计成本</dt><dd>${stats.cost.toFixed(6)}</dd></div>
            <div><dt>Token</dt><dd>{stats.tokens.toLocaleString()}</dd></div>
            <div><dt>缓存 / 切换</dt><dd>{stats.cacheHits} / {stats.switches}</dd></div>
          </dl>
          <span className={`gateway-status ${gatewayOnline === true ? "online" : gatewayOnline === false ? "offline" : "checking"}`}>
            <i /> {gatewayOnline === true ? "Gateway 在线" : gatewayOnline === false ? "Gateway 离线" : "检查中"}
          </span>
        </header>
        {estimatedContext >= CONTEXT_WARNING_TOKENS && (
          <div className="context-warning" role="status">
            <span>上下文估算 {estimatedContext.toLocaleString()} / {CONTEXT_LIMIT_ESTIMATE.toLocaleString()} token，已接近保守上限；PolyGate 不会静默截断历史。</span>
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
