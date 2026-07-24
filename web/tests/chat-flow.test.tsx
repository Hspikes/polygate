import { act, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import App from "../src/App";

const requestIdFor = (turn: number) => `req_${turn.toString(16).padStart(32, "0")}`;

const streamResponse = (answer: string, requestId: string) => {
  const chunk = JSON.stringify({
    choices: [{ delta: { content: answer }, finish_reason: "stop" }],
  });
  return new Response(`data: ${chunk}\n\ndata: [DONE]\n\n`, {
    status: 200,
    headers: {
      "Content-Type": "text/event-stream",
      "X-PolyGate-Request-ID": requestId,
      "X-PolyGate-Provider": "mock-a",
    },
  });
};

const decisionResponse = (answer: string, requestId: string) => new Response(JSON.stringify({
  schema_version: 1,
  request_id: requestId,
  outcome: "success",
  chosen_provider: "mock-a",
  initial_provider: "mock-a",
  reason: "test route",
  cache_hit: false,
  stream: true,
  cost_estimate_usd: 0.0001,
  latency_ms: 10,
  tokens: { input: 5, output: Math.max(1, answer.length) },
  retries: 0,
  failover_from: null,
  failover_count: 0,
  created_at: "2026-07-24T03:00:00Z",
  expires_at: "2026-07-24T04:00:00Z",
}), {
  status: 200,
  headers: { "Content-Type": "application/json" },
});

describe("chat flow", () => {
  beforeEach(() => {
    let turn = 0;
    const answers = new Map<string, string>();
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url === "/api/v1/models") return new Response('{"object":"list","data":[]}', { status: 200 });
      if (url === "/api/v1/chat/completions") {
        turn += 1;
        const requestId = requestIdFor(turn);
        const answer = `answer ${turn}`;
        answers.set(requestId, answer);
        return streamResponse(answer, requestId);
      }
      if (url.startsWith("/api/v1/decisions/")) {
        const requestId = url.split("/").at(-1)!;
        const answer = answers.get(requestId);
        if (!answer) return new Response('{"detail":"not found"}', { status: 404 });
        return decisionResponse(answer, requestId);
      }
      throw new Error(`unexpected fetch ${url}`);
    }));
  });

  it("sends complete history on the second turn and supports Enter", async () => {
    const user = userEvent.setup();
    render(<App />);
    const composer = await screen.findByRole("textbox", { name: "消息内容" });
    await user.type(composer, "first{enter}");
    expect(await screen.findByText("answer 1")).toBeInTheDocument();
    expect(screen.queryByText("PolyGate 回复")).not.toBeInTheDocument();
    expect(screen.queryByText("由智能路由选定模型生成")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "加载离线演示" })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "重新生成回答" })).toBeInTheDocument();
    expect(screen.getAllByText("路由策略")).toHaveLength(1);
    expect(screen.getByRole("button", { name: /路由偏好/ })).toHaveAttribute("aria-expanded", "false");
    expect(screen.getByRole("button", { name: /路由策略/ })).toHaveAttribute("aria-expanded", "false");
    await user.type(composer, "second{enter}");
    expect(await screen.findByText("answer 2")).toBeInTheDocument();
    expect(screen.queryByText("PolyGate 回复")).not.toBeInTheDocument();
    expect(screen.getAllByText("路由策略")).toHaveLength(2);

    const chatCalls = vi.mocked(fetch).mock.calls.filter(([url]) => String(url) === "/api/v1/chat/completions");
    const request = JSON.parse(String((chatCalls[1][1] as RequestInit).body)) as { messages: Array<{ role: string; content: string }> };
    expect(request.messages).toEqual([
      { role: "user", content: "first" },
      { role: "assistant", content: "answer 1" },
      { role: "user", content: "second" },
    ]);
  });

  it("collapses routing preferences on pointer leave after a setting was changed", async () => {
    const user = userEvent.setup();
    render(<App />);

    const trigger = await screen.findByRole("button", { name: /路由偏好/ });
    const panel = trigger.closest(".routing-panel");
    expect(panel).not.toBeNull();

    await user.hover(panel!);
    await waitFor(() => expect(trigger).toHaveAttribute("aria-expanded", "true"));
    const qualitySelect = screen.getByRole("combobox", { name: "质量" });
    await user.selectOptions(qualitySelect, "high");
    expect(qualitySelect).toHaveValue("high");

    await user.unhover(panel!);
    expect(trigger).toHaveAttribute("aria-expanded", "false");
    expect(panel).not.toHaveClass("is-expanded");
  });

  it("does not return Chrome 150 scroll promises from the message effect", async () => {
    vi.spyOn(Element.prototype, "scrollIntoView").mockImplementation(
      () => Promise.resolve(true) as never,
    );
    const user = userEvent.setup();
    render(<App />);

    const composer = await screen.findByRole("textbox", { name: "消息内容" });
    await user.type(composer, "chrome 150{enter}");

    expect(await screen.findByText("answer 1")).toBeInTheDocument();
    expect(composer).toBeVisible();
    expect(Element.prototype.scrollIntoView).toHaveBeenCalled();
  });

  it("collapses and reopens the desktop conversation rail", async () => {
    const user = userEvent.setup();
    render(<App />);

    const sidebar = await screen.findByRole("complementary", { name: "会话列表" });
    await user.click(screen.getByRole("button", { name: "收起会话栏" }));
    expect(sidebar).toHaveClass("collapsed");

    await user.click(screen.getByRole("button", { name: "展开会话栏" }));
    expect(sidebar).not.toHaveClass("collapsed");
  });

  it("dismisses a conversation menu from outside or with Escape", async () => {
    const user = userEvent.setup();
    render(<App />);

    const manage = await screen.findByRole("button", { name: /管理 新会话/ });
    await user.click(manage);
    expect(screen.getByRole("menuitem", { name: "重命名" })).toBeInTheDocument();

    await user.click(document.body);
    expect(screen.queryByRole("menuitem", { name: "重命名" })).not.toBeInTheDocument();

    await user.click(manage);
    await user.keyboard("{Escape}");
    expect(screen.queryByRole("menuitem", { name: "重命名" })).not.toBeInTheDocument();
  });

  it("uses Shift+Enter for a newline without sending", async () => {
    const user = userEvent.setup();
    render(<App />);
    const composer = await screen.findByRole("textbox", { name: "消息内容" });
    await user.type(composer, "line one{shift>}{enter}{/shift}line two");
    expect(composer).toHaveValue("line one\nline two");
    await waitFor(() => expect(vi.mocked(fetch).mock.calls.filter(([url]) => String(url).includes("completions"))).toHaveLength(0));
  });

  it("keeps correct history across ten sequential turns", async () => {
    const user = userEvent.setup();
    render(<App />);
    const composer = await screen.findByRole("textbox", { name: "消息内容" });
    for (let turn = 1; turn <= 10; turn += 1) {
      await user.type(composer, `turn ${turn}{enter}`);
      expect(await screen.findByText(`answer ${turn}`)).toBeInTheDocument();
    }
    const chatCalls = vi.mocked(fetch).mock.calls.filter(([url]) => String(url) === "/api/v1/chat/completions");
    const finalRequest = JSON.parse(String((chatCalls[9][1] as RequestInit).body)) as { messages: unknown[] };
    expect(finalRequest.messages).toHaveLength(19);
  });

  it("surfaces provider errors and retries without duplicating the user message", async () => {
    let attempts = 0;
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url === "/api/v1/models") return new Response("{}", { status: 200 });
      if (url.startsWith("/api/v1/decisions/")) {
        return decisionResponse("recovered", requestIdFor(1));
      }
      attempts += 1;
      if (attempts === 1) {
        return new Response('{"detail":"provider mock-a failed"}', {
          status: 502,
          headers: { "X-PolyGate-Request-ID": "req-provider-failure" },
        });
      }
      return streamResponse("recovered", requestIdFor(1));
    }));
    const user = userEvent.setup();
    const writeText = vi.spyOn(navigator.clipboard, "writeText").mockResolvedValue(undefined);
    const { container } = render(<App />);
    await user.type(await screen.findByRole("textbox", { name: "消息内容" }), "retry me{enter}");
    expect(await screen.findByText("模型服务错误")).toBeInTheDocument();
    expect(screen.getByText("req-provider-failure")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "复制 request ID" }));
    expect(writeText).toHaveBeenCalledWith("req-provider-failure");
    await user.click(screen.getByRole("button", { name: "重试" }));
    expect(await screen.findByText("recovered")).toBeInTheDocument();
    expect(container.querySelectorAll(".user-message .message-bubble")).toHaveLength(1);
    expect(container.querySelector(".user-message .message-bubble")).toHaveTextContent("retry me");
  });

  it("aborts an in-flight request and keeps it retryable", async () => {
    vi.stubGlobal("fetch", vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      if (String(input) === "/api/v1/models") return Promise.resolve(new Response("{}", { status: 200 }));
      return new Promise<Response>((_resolve, reject) => {
        init?.signal?.addEventListener("abort", () => reject(new DOMException("aborted", "AbortError")));
      });
    }));
    const user = userEvent.setup();
    render(<App />);
    await user.type(await screen.findByRole("textbox", { name: "消息内容" }), "cancel me{enter}");
    await user.click(await screen.findByRole("button", { name: "取消生成" }));
    expect(await screen.findByText("生成已停止")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "重试" })).toBeInTheDocument();
  });

  it("renders deltas before DONE and persists only after the stream is stable", async () => {
    const requestId = requestIdFor(99);
    let streamController: ReadableStreamDefaultController<Uint8Array> | undefined;
    const storageWrite = vi.spyOn(Storage.prototype, "setItem");
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url === "/api/v1/models") return new Response("{}", { status: 200 });
      if (url === "/api/v1/chat/completions") {
        return new Response(new ReadableStream<Uint8Array>({
          start(controller) {
            streamController = controller;
          },
        }), {
          status: 200,
          headers: {
            "Content-Type": "text/event-stream",
            "X-PolyGate-Request-ID": requestId,
          },
        });
      }
      if (url === `/api/v1/decisions/${requestId}`) {
        return decisionResponse("partial done", requestId);
      }
      throw new Error(`unexpected fetch ${url}`);
    }));

    const user = userEvent.setup();
    render(<App />);
    const composer = await screen.findByRole("textbox", { name: "消息内容" });
    await waitFor(() => expect(storageWrite).toHaveBeenCalled());
    const writesBeforeRequest = storageWrite.mock.calls.length;
    await user.type(composer, "stream visibly{enter}");
    await waitFor(() => expect(streamController).toBeDefined());

    await act(async () => {
      streamController!.enqueue(new TextEncoder().encode(
        'data: {"choices":[{"delta":{"content":"partial "},"finish_reason":null}]}\n\n',
      ));
    });
    expect(await screen.findByText("partial")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "取消生成" })).toBeInTheDocument();
    expect(storageWrite).toHaveBeenCalledTimes(writesBeforeRequest);

    await act(async () => {
      streamController!.enqueue(new TextEncoder().encode(
        'data: {"choices":[{"delta":{"content":"done"},"finish_reason":"stop"}]}\n\n'
        + 'data: [DONE]\n\n',
      ));
      streamController!.close();
    });

    expect(await screen.findByText("partial done")).toBeInTheDocument();
    expect(await screen.findByText("路由策略")).toBeInTheDocument();
    await waitFor(() => expect(storageWrite.mock.calls.length).toBeGreaterThan(writesBeforeRequest));
  });

  it("keeps a truncated answer visible and marks it incomplete", async () => {
    const requestId = requestIdFor(100);
    const chunk = 'data: {"choices":[{"delta":{"content":"kept partial"},"finish_reason":null}]}\n\n';
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => {
      if (String(input) === "/api/v1/models") return new Response("{}", { status: 200 });
      return new Response(chunk, {
        status: 200,
        headers: {
          "Content-Type": "text/event-stream",
          "X-PolyGate-Request-ID": requestId,
        },
      });
    }));

    const user = userEvent.setup();
    render(<App />);
    await user.type(await screen.findByRole("textbox", { name: "消息内容" }), "truncate{enter}");

    expect(await screen.findByText("kept partial")).toBeInTheDocument();
    expect(await screen.findByText("回答不完整")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "重试" })).toBeInTheDocument();
  });

  it("offers the local fixture only when the gateway is offline", async () => {
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => {
      if (String(input) === "/api/v1/models") return new Response("{}", { status: 401 });
      throw new Error(`unexpected fetch ${String(input)}`);
    }));

    render(<App />);

    expect(await screen.findByRole("button", { name: "加载离线演示" })).toBeInTheDocument();
  });
});
