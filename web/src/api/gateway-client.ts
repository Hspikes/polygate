import {
  decisionRecordSchema,
  gatewayResponseSchema,
  type GatewayRequest,
} from "../domain/gateway";
import type { DecisionCardData, MessageError } from "../domain/conversation";

const CHAT_ENDPOINT = "/api/v1/chat/completions";
const DECISION_ENDPOINT = "/api/v1/decisions";

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

function abortError(signal: AbortSignal): DOMException {
  return signal.reason instanceof DOMException && signal.reason.name === "AbortError"
    ? signal.reason
    : new DOMException("The request was aborted", "AbortError");
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

class SseEventDecoder {
  private buffer = "";
  private dataLines: string[] = [];

  push(text: string): string[] {
    this.buffer += text;
    const events: string[] = [];

    while (this.buffer) {
      let lineEnd = -1;
      for (let index = 0; index < this.buffer.length; index += 1) {
        if (this.buffer[index] === "\n" || this.buffer[index] === "\r") {
          lineEnd = index;
          break;
        }
      }
      if (lineEnd < 0) break;
      if (
        this.buffer[lineEnd] === "\r"
        && lineEnd === this.buffer.length - 1
      ) break;

      const line = this.buffer.slice(0, lineEnd);
      const terminatorLength =
        this.buffer[lineEnd] === "\r" && this.buffer[lineEnd + 1] === "\n"
          ? 2
          : 1;
      this.buffer = this.buffer.slice(lineEnd + terminatorLength);
      this.consumeLine(line, events);
    }

    return events;
  }

  private consumeLine(line: string, events: string[]): void {
    if (line === "") {
      if (this.dataLines.length > 0) events.push(this.dataLines.join("\n"));
      this.dataLines = [];
      return;
    }
    if (line.startsWith(":")) return;
    if (line === "data") {
      this.dataLines.push("");
      return;
    }
    if (!line.startsWith("data:")) return;
    const value = line.slice(5);
    this.dataLines.push(value.startsWith(" ") ? value.slice(1) : value);
  }
}

function streamError(
  message: string,
  requestId: string,
  hasPartialAnswer: boolean,
): GatewayClientError {
  return new GatewayClientError({
    kind: hasPartialAnswer ? "partial" : "validation",
    message,
    requestId,
  });
}

function abortableDelay(milliseconds: number, signal: AbortSignal): Promise<void> {
  if (signal.aborted) return Promise.reject(abortError(signal));
  return new Promise((resolve, reject) => {
    const timer = window.setTimeout(() => {
      signal.removeEventListener("abort", onAbort);
      resolve();
    }, milliseconds);
    const onAbort = () => {
      window.clearTimeout(timer);
      reject(abortError(signal));
    };
    signal.addEventListener("abort", onAbort, { once: true });
  });
}

async function requestDecisionCard(
  requestId: string,
  signal: AbortSignal,
): Promise<DecisionCardData> {
  const retryDelays = [0, 40, 80, 160];
  for (let attempt = 0; attempt < retryDelays.length; attempt += 1) {
    if (retryDelays[attempt] > 0) await abortableDelay(retryDelays[attempt], signal);
    let response: Response;
    try {
      response = await fetch(`${DECISION_ENDPOINT}/${encodeURIComponent(requestId)}`, {
        signal,
      });
    } catch {
      if (signal.aborted) throw abortError(signal);
      throw new GatewayClientError({
        kind: "decision",
        message: "回答已生成，但暂时无法读取最终路由记录。",
        requestId,
      });
    }

    if (response.status === 404 && attempt < retryDelays.length - 1) {
      await response.body?.cancel();
      continue;
    }
    if (!response.ok) {
      throw new GatewayClientError({
        kind: "decision",
        message: "回答已生成，但最终路由记录暂不可用。",
        status: response.status,
        requestId,
      });
    }

    let body: unknown;
    try {
      body = await response.json();
    } catch {
      throw new GatewayClientError({
        kind: "decision",
        message: "回答已生成，但最终路由记录不是有效的 JSON。",
        status: response.status,
        requestId,
      });
    }
    const parsed = decisionRecordSchema.safeParse(body);
    if (!parsed.success || parsed.data.requestId !== requestId) {
      throw new GatewayClientError({
        kind: "decision",
        message: "回答已生成，但最终路由记录不符合客户端契约。",
        status: response.status,
        requestId,
      });
    }
    return parsed.data;
  }
  throw new GatewayClientError({
    kind: "decision",
    message: "回答已生成，但最终路由记录尚未就绪。",
    requestId,
  });
}

export interface StreamingCompletionResult {
  answer: string;
  decisionCard?: DecisionCardData;
  warning?: MessageError;
}

export async function requestStreamingCompletion(
  payload: GatewayRequest,
  signal: AbortSignal,
  onDelta: (delta: string) => void,
): Promise<StreamingCompletionResult> {
  let response: Response;
  try {
    response = await fetch(CHAT_ENDPOINT, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
      signal,
    });
  } catch {
    if (signal.aborted) throw abortError(signal);
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
  if (!requestId) {
    throw new GatewayClientError({
      kind: "validation",
      message: "网关流式响应缺少 request ID。",
      status: response.status,
    });
  }
  if (!response.headers.get("Content-Type")?.toLowerCase().includes("text/event-stream")) {
    throw new GatewayClientError({
      kind: "validation",
      message: "网关没有返回标准 SSE 流。",
      status: response.status,
      requestId,
    });
  }
  if (!response.body) {
    throw new GatewayClientError({
      kind: "validation",
      message: "当前浏览器无法读取网关流式响应。",
      status: response.status,
      requestId,
    });
  }

  const reader = response.body.getReader();
  const textDecoder = new TextDecoder();
  const eventDecoder = new SseEventDecoder();
  let answer = "";
  let sawDone = false;
  let reachedEof = false;
  const cancelReader = () => {
    void reader.cancel(abortError(signal)).catch(() => undefined);
  };
  signal.addEventListener("abort", cancelReader, { once: true });

  const consumeData = (data: string) => {
    if (sawDone) return;
    if (data.trim() === "[DONE]") {
      sawDone = true;
      return;
    }
    let chunk: unknown;
    try {
      chunk = JSON.parse(data);
    } catch {
      throw streamError("网关返回了无法解析的 SSE 数据。", requestId, answer.length > 0);
    }
    if (!chunk || typeof chunk !== "object" || "error" in chunk) {
      throw streamError("上游模型在流式回答中返回错误。", requestId, answer.length > 0);
    }
    const choices = (chunk as { choices?: unknown }).choices;
    if (!Array.isArray(choices)) return;
    choices.forEach((choice) => {
      if (!choice || typeof choice !== "object") return;
      const delta = (choice as { delta?: unknown }).delta;
      if (!delta || typeof delta !== "object") return;
      const content = (delta as { content?: unknown }).content;
      if (typeof content !== "string" || content.length === 0) return;
      answer += content;
      onDelta(content);
    });
  };

  try {
    while (true) {
      const { value, done } = await reader.read();
      if (done) {
        reachedEof = true;
        break;
      }
      const text = textDecoder.decode(value, { stream: true });
      eventDecoder.push(text).forEach(consumeData);
      if (signal.aborted) throw abortError(signal);
    }
    eventDecoder.push(textDecoder.decode()).forEach(consumeData);
  } catch (error) {
    if (signal.aborted) throw abortError(signal);
    if (error instanceof GatewayClientError) throw error;
    throw new GatewayClientError({
      kind: answer.length > 0 ? "partial" : "network",
      message: answer.length > 0
        ? "流式回答意外中断，以下内容可能不完整。"
        : "读取网关流式响应失败，请重试。",
      requestId,
    });
  } finally {
    signal.removeEventListener("abort", cancelReader);
    if (!reachedEof) await reader.cancel().catch(() => undefined);
    reader.releaseLock();
  }

  if (signal.aborted) throw abortError(signal);
  if (!sawDone) {
    throw new GatewayClientError({
      kind: "partial",
      message: "流式回答未正常结束，以下内容可能不完整。",
      requestId,
    });
  }
  if (!answer) {
    throw new GatewayClientError({
      kind: "validation",
      message: "网关流式响应没有可显示的文本内容。",
      requestId,
    });
  }

  try {
    const decisionCard = await requestDecisionCard(requestId, signal);
    return { answer, decisionCard };
  } catch (error) {
    if (signal.aborted) throw abortError(signal);
    if (error instanceof GatewayClientError && error.details.kind === "decision") {
      return { answer, warning: error.details };
    }
    throw error;
  }
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
