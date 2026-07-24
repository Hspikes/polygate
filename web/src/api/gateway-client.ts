import { gatewayResponseSchema, type GatewayRequest } from "../domain/gateway";
import type { MessageError } from "../domain/conversation";

const CHAT_ENDPOINT = "/api/v1/chat/completions";

export class GatewayClientError extends Error {
  constructor(public readonly details: MessageError) {
    super(details.message);
  }
}

function classifyError(status: number, detail: string): MessageError["kind"] {
  if (status === 401) return "auth";
  if (status === 403 || /privacy/i.test(detail)) return "validation";
  if (status === 422) return "validation";
  if (status === 429) return /budget|cost/i.test(detail) ? "budget" : "rate_limit";
  if (status === 502) return "provider";
  if (status === 503) return "routing";
  if (status === 504) return "timeout";
  return "unknown";
}

async function responseDetail(response: Response): Promise<string> {
  const text = await response.text();
  try {
    const body = JSON.parse(text) as {
      detail?: unknown;
      error?: { message?: unknown };
    };
    if (typeof body.detail === "string") return body.detail;
    if (typeof body.error?.message === "string") return body.error.message;
    return text;
  } catch {
    return text;
  }
}

function requestIdFrom(response: Response): string | undefined {
  return response.headers.get("X-PolyGate-Request-ID")?.trim() || undefined;
}

function userMessage(
  kind: MessageError["kind"],
  detail: string,
  status: number,
): string {
  const fallback = detail || `网关返回 HTTP ${status}`;
  switch (kind) {
    case "auth":
      return "Web 凭证未配置或已失效，请联系管理员检查服务端配置。";
    case "validation":
      return detail ? `请求内容未通过校验：${detail}` : "请求内容未通过校验，请检查后重试。";
    case "budget":
      return detail ? `请求受预算约束：${detail}` : "请求受预算约束，请调整成本上限后重试。";
    case "rate_limit":
      return "请求触发限流，请稍后重试。";
    case "provider":
      return detail ? `上游模型调用失败：${detail}` : "上游模型调用失败，请稍后重试。";
    case "routing":
      return detail ? `没有满足约束的可用路由：${detail}` : "没有满足约束的可用路由，请调整路由偏好后重试。";
    case "timeout":
      return "Provider 调用或流式启动超过时间预算，请重试。";
    default:
      return fallback;
  }
}

export async function requestCompletion(payload: GatewayRequest, signal: AbortSignal) {
  let response: Response;
  try {
    response = await fetch(CHAT_ENDPOINT, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
      signal,
    });
  } catch (error) {
    if (error instanceof DOMException && error.name === "AbortError") throw error;
    throw new GatewayClientError({
      kind: "network",
      message: "无法连接网关，请检查服务和网络后重试。",
    });
  }

  const requestId = requestIdFrom(response);
  if (!response.ok) {
    const detail = await responseDetail(response);
    const kind = classifyError(response.status, detail);
    throw new GatewayClientError({
      kind,
      message: userMessage(kind, detail, response.status),
      status: response.status,
      requestId,
    });
  }

  let responseBody: unknown;
  try {
    responseBody = await response.json();
  } catch {
    throw new GatewayClientError({
      kind: "validation",
      message: "网关响应不是有效的 JSON。",
      status: response.status,
      requestId,
    });
  }
  const parsed = gatewayResponseSchema.safeParse(responseBody);
  if (!parsed.success) {
    throw new GatewayClientError({
      kind: "validation",
      message: "网关响应不符合客户端契约。",
      status: response.status,
      requestId,
    });
  }
  return requestId
    ? {
        ...parsed.data,
        decisionCard: { ...parsed.data.decisionCard, requestId },
      }
    : parsed.data;
}

export async function gatewayHealth(signal?: AbortSignal): Promise<boolean> {
  try {
    // /v1/models is authenticated, so this cannot report a false-positive
    // "online" state when the Nginx-injected Web credential is missing.
    const response = await fetch("/api/v1/models", { signal });
    return response.ok;
  } catch {
    return false;
  }
}
