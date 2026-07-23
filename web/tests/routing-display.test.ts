import { describe, expect, it } from "vitest";
import { formatUsd, humanizeRoutingReason, routingSummary } from "../src/domain/routing-display";

describe("routing display helpers", () => {
  it("keeps tiny non-zero costs visible", () => {
    expect(formatUsd(0)).toBe("$0");
    expect(formatUsd(0.0000001)).toBe("< $0.000001");
    expect(formatUsd(0.000021)).toBe("$0.000021");
  });

  it("summarizes routing preferences in user-facing language", () => {
    expect(routingSummary({
      model: "auto",
      quality: "balanced",
      privacy: "standard",
      maxCostUsd: 0.002,
      latencyTargetMs: 3000,
    })).toBe("均衡 · 标准隐私 · $0.002 · 3,000 ms");
  });

  it("humanizes known backend vocabulary without changing the decision itself", () => {
    expect(humanizeRoutingReason("quality=balanced → Provider cost gap; selected mock-b"))
      .toBe("质量=均衡 → 模型服务 cost gap；selected mock-b");
  });
});
