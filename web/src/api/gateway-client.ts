import { gatewayResponseSchema, type GatewayRequest } from "../domain/gateway";
import type { MessageError } from "../domain/conversation";

const CHAT_ENDPOINT = "/api/v1/chat/completions";

export class GatewayClientError extends Error {
  constructor(public readonly details: MessageError) {
    super(details.message);
  }
}

function classifyError(status: number, detail: string): MessageError["kind"] {
  if (status === 403 || /privacy/i.test(detail)) return "validation";
  if (status === 422) return "validation";
  if (status === 429 || /budget|cost/i.test(detail)) return "budget";
  if (status === 502) return "provider";
  if (status === 503) return "routing";
  return "unknown";
}

async function responseDetail(response: Response): Promise<string> {
  const text = await response.text();
  try {
    const body = JSON.parse(text) as { detail?: unknown };
    return typeof body.detail === "string" ? body.detail : text;
  } catch {
    return text;
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
      message: "无法连接 Gateway，请检查服务和网络后重试。",
    });
  }

  if (!response.ok) {
    const detail = await responseDetail(response);
    throw new GatewayClientError({
      kind: classifyError(response.status, detail),
      message: detail || `Gateway 返回 HTTP ${response.status}`,
      status: response.status,
    });
  }

  const parsed = gatewayResponseSchema.safeParse(await response.json());
  if (!parsed.success) {
    throw new GatewayClientError({
      kind: "validation",
      message: "Gateway 响应不符合客户端契约。",
    });
  }
  return parsed.data;
}

export async function gatewayHealth(signal?: AbortSignal): Promise<boolean> {
  try {
    const response = await fetch("/api/health", { signal });
    return response.ok;
  } catch {
    return false;
  }
}
