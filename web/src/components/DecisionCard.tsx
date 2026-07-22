import type { DecisionCardData, RoutingSettings } from "../domain/conversation";
import { RouteIcon } from "./icons";

export function DecisionCard({ card, settings }: { card: DecisionCardData; settings?: RoutingSettings }) {
  return (
    <details className="decision-card">
      <summary>
        <span className="decision-title"><RouteIcon /> Routing note</span>
        <span className="decision-badges">
          <span className="provider-badge">{card.chosenProvider}</span>
          <span className={card.cacheHit ? "cache-hit" : "cache-miss"}>
            {card.cacheHit ? "Cache hit" : "Cache miss"}
          </span>
        </span>
      </summary>
      <div className="decision-body">
        <dl className="decision-metrics">
          <div><dt>成本</dt><dd>${card.costEstimateUsd.toFixed(6)}</dd></div>
          <div><dt>延迟</dt><dd>{card.latencyMs.toLocaleString()} ms</dd></div>
          <div><dt>输入 token</dt><dd>{card.tokens.input.toLocaleString()}</dd></div>
          <div><dt>输出 token</dt><dd>{card.tokens.output.toLocaleString()}</dd></div>
        </dl>
        <p className="decision-reason"><strong>为什么这样路由？</strong>{card.reason}</p>
        <div className="decision-trace">
          <span>Request <code>{card.requestId}</code></span>
          <span>{settings ? `${settings.quality} · ${settings.privacy} · ${settings.latencyTargetMs} ms` : ""}</span>
          {card.failoverFrom && <span>Failover from {card.failoverFrom}</span>}
          <span>Retries {card.retries}</span>
        </div>
      </div>
    </details>
  );
}
