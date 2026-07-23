import type { Privacy, Quality, RoutingSettings } from "./conversation";

export const qualityLabel: Record<Quality, string> = {
  balanced: "均衡",
  high: "高质量",
  cheap: "低成本",
};

export const privacyLabel: Record<Privacy, string> = {
  standard: "标准隐私",
  high: "高隐私",
};

export function formatUsd(value: number): string {
  if (value === 0) return "$0";
  if (value < 0.000001) return "< $0.000001";
  if (value < 0.01) return `$${value.toFixed(6).replace(/0+$/, "").replace(/\.$/, "")}`;
  return new Intl.NumberFormat("en-US", {
    style: "currency",
    currency: "USD",
    maximumFractionDigits: 4,
  }).format(value);
}

export function routingSummary(settings: RoutingSettings): string {
  return `${qualityLabel[settings.quality]} · ${privacyLabel[settings.privacy]} · ${formatUsd(settings.maxCostUsd)} · ${settings.latencyTargetMs.toLocaleString()} ms`;
}

export function humanizeRoutingReason(reason: string): string {
  return reason
    .replace(/\bquality=(balanced|high|cheap)\b/g, (_match, quality: Quality) => `质量=${qualityLabel[quality]}`)
    .replace(/\bProvider\b/g, "模型服务")
    .replace(/;\s*/g, "；");
}
