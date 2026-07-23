import { useId, useRef } from "react";
import type { Conversation, Privacy, Quality } from "../domain/conversation";
import { routingSummary } from "../domain/routing-display";
import { useHoverDisclosure } from "../hooks/useHoverDisclosure";
import { useConversations } from "../store/ConversationProvider";
import { ChevronIcon, RouteIcon } from "./icons";

export function RoutingPanel({ conversation }: { conversation: Conversation }) {
  const { updateSettings, setStorageMode } = useConversations();
  const disclosure = useHoverDisclosure();
  const panel = useRef<HTMLElement>(null);
  const trigger = useRef<HTMLButtonElement>(null);
  const contentId = useId();
  const settings = conversation.settings;

  const collapseAfterPointerLeaves = () => {
    const focusIsInsidePanel = panel.current?.contains(document.activeElement) ?? false;
    disclosure.closeFromHover();
    if (focusIsInsidePanel && document.activeElement !== trigger.current) {
      window.requestAnimationFrame(() => trigger.current?.focus({ preventScroll: true }));
    }
  };

  return (
    <section
      ref={panel}
      className={`routing-panel glass-card${disclosure.expanded ? " is-expanded" : ""}`}
      onMouseEnter={disclosure.openFromHover}
      onMouseLeave={collapseAfterPointerLeaves}
      onBlur={(event) => {
        if (!event.currentTarget.contains(event.relatedTarget)) disclosure.close();
      }}
    >
      <button
        ref={trigger}
        className="routing-panel-trigger"
        type="button"
        aria-expanded={disclosure.expanded}
        aria-controls={contentId}
        onClick={(event) => disclosure.toggleFromTrigger(event.detail)}
      >
        <span className="routing-panel-label"><RouteIcon /> 路由偏好</span>
        <span className="routing-panel-meta">
          <small>{routingSummary(settings)}</small>
          <ChevronIcon className="disclosure-chevron" />
        </span>
      </button>
      <div
        id={contentId}
        className="routing-panel-content"
        aria-hidden={!disclosure.expanded}
        inert={!disclosure.expanded}
      >
        <div className="routing-panel-content-inner">
          <div className="routing-fields">
            <label>
              <span>质量</span>
              <select value={settings.quality} onChange={(event) => updateSettings({ quality: event.target.value as Quality })}>
                <option value="balanced">均衡</option>
                <option value="high">最高质量</option>
                <option value="cheap">最低成本</option>
              </select>
            </label>
            <label>
              <span>隐私</span>
              <select value={settings.privacy} onChange={(event) => updateSettings({ privacy: event.target.value as Privacy })}>
                <option value="standard">标准</option>
                <option value="high">高隐私</option>
              </select>
            </label>
            <label>
              <span>成本上限 (USD)</span>
              <input type="number" min="0" step="0.0001" value={settings.maxCostUsd} onChange={(event) => updateSettings({ maxCostUsd: Math.max(0, Number(event.target.value)) })} />
            </label>
            <label>
              <span>延迟目标 (ms)</span>
              <input type="number" min="1" step="100" value={settings.latencyTargetMs} onChange={(event) => updateSettings({ latencyTargetMs: Math.max(1, Math.round(Number(event.target.value))) })} />
            </label>
          </div>
          <label className="storage-toggle">
            <input
              type="checkbox"
              checked={conversation.storageMode === "persistent"}
              disabled={settings.privacy === "high"}
              onChange={(event) => setStorageMode(event.target.checked ? "persistent" : "ephemeral")}
            />
            <span>
              保存本会话历史
              <small>{settings.privacy === "high" ? "高隐私模式默认不保存，刷新后会清除" : "仅存储在当前浏览器，不包含任何 API Key"}</small>
            </span>
          </label>
          <p className="policy-note">偏好只影响后续请求；每条历史回答会保留它实际使用的设置。</p>
        </div>
      </div>
    </section>
  );
}
