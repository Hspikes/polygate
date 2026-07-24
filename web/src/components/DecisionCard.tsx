import { useId } from "react";
import type { DecisionCardData, RoutingSettings } from "../domain/conversation";
import { formatUsd, humanizeRoutingReason, privacyLabel, qualityLabel } from "../domain/routing-display";
import { useHoverDisclosure } from "../hooks/useHoverDisclosure";
import { ChevronIcon, RouteIcon } from "./icons";

export function DecisionCard({ card, settings }: { card: DecisionCardData; settings?: RoutingSettings }) {
  const disclosure = useHoverDisclosure();
  const contentId = useId();

  return (
    <section
      className={`decision-card glass-card${disclosure.expanded ? " is-expanded" : ""}`}
      aria-label="路由策略卡片"
      onMouseEnter={disclosure.openFromHover}
      onMouseLeave={disclosure.closeFromHover}
      onBlur={(event) => {
        if (!event.currentTarget.contains(event.relatedTarget)) disclosure.close();
      }}
    >
      <button
        className="decision-header"
        type="button"
        aria-expanded={disclosure.expanded}
        aria-controls={contentId}
        onClick={(event) => disclosure.toggleFromTrigger(event.detail)}
      >
        <span className="decision-title">
          <RouteIcon />
          <span><strong>路由策略</strong><small>本轮模型选择依据</small></span>
        </span>
        <span className="decision-actions">
          <span className="decision-badges">
            <span className="provider-badge">{card.chosenProvider}</span>
            <span className={card.cacheHit ? "cache-hit" : "cache-miss"}>
              {card.cacheHit ? "缓存命中" : "缓存未命中"}
            </span>
          </span>
          <ChevronIcon className="disclosure-chevron" />
        </span>
      </button>
      <div
        id={contentId}
        className="decision-body-collapsible"
        aria-hidden={!disclosure.expanded}
        inert={!disclosure.expanded}
      >
        <div className="decision-body">
          <dl className="decision-metrics">
            <div><dt>成本</dt><dd>{formatUsd(card.costEstimateUsd)}</dd></div>
            <div><dt>延迟</dt><dd>{card.latencyMs.toLocaleString()} ms</dd></div>
            <div><dt>输入 Token</dt><dd>{card.tokens.input.toLocaleString()}</dd></div>
            <div><dt>输出 Token</dt><dd>{card.tokens.output.toLocaleString()}</dd></div>
          </dl>
          <p className="decision-reason"><strong>为什么这样路由？</strong>{humanizeRoutingReason(card.reason)}</p>
          <div className="decision-trace">
            <span>请求 <code>{card.requestId}</code></span>
            <span>{settings ? `${qualityLabel[settings.quality]} · ${privacyLabel[settings.privacy]} · ${settings.latencyTargetMs.toLocaleString()} ms` : ""}</span>
            {card.failoverFrom && <span>故障转移自 {card.failoverFrom}</span>}
            {card.failoverCount !== undefined && card.failoverCount > 1 && (
              <span>共故障转移 {card.failoverCount} 次</span>
            )}
            <span>重试 {card.retries} 次</span>
          </div>
        </div>
      </div>
    </section>
  );
}
