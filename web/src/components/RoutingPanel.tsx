import type { Conversation, Privacy, Quality } from "../domain/conversation";
import { useConversations } from "../store/ConversationProvider";
import { RouteIcon } from "./icons";

export function RoutingPanel({ conversation }: { conversation: Conversation }) {
  const { updateSettings, setStorageMode } = useConversations();
  const settings = conversation.settings;
  return (
    <details className="routing-panel">
      <summary>
        <span><RouteIcon /> 路由偏好</span>
        <small>{settings.quality} · {settings.privacy} · ${settings.maxCostUsd} · {settings.latencyTargetMs} ms</small>
      </summary>
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
          <span>最高成本 (USD)</span>
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
    </details>
  );
}
