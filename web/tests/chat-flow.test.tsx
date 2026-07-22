import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import App from "../src/App";

const responseBody = (answer: string) => ({
  answer,
  polygate: {
    chosen_provider: "mock-a",
    reason: "test route",
    cache_hit: false,
    cost_estimate_usd: 0.0001,
    latency_ms: 10,
    tokens: { input: 5, output: 2 },
    retries: 0,
    failover_from: null,
    request_id: `req-${answer}`,
  },
});

describe("chat flow", () => {
  beforeEach(() => {
    let turn = 0;
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url === "/api/health") return new Response('{"status":"ok"}', { status: 200 });
      if (url === "/api/v1/chat/completions") {
        turn += 1;
        return new Response(JSON.stringify(responseBody(`answer ${turn}`)), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        });
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
    await user.type(composer, "second{enter}");
    expect(await screen.findByText("answer 2")).toBeInTheDocument();

    const chatCalls = vi.mocked(fetch).mock.calls.filter(([url]) => String(url) === "/api/v1/chat/completions");
    const request = JSON.parse(String((chatCalls[1][1] as RequestInit).body)) as { messages: Array<{ role: string; content: string }> };
    expect(request.messages).toEqual([
      { role: "user", content: "first" },
      { role: "assistant", content: "answer 1" },
      { role: "user", content: "second" },
    ]);
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
      if (String(input) === "/api/health") return new Response("{}", { status: 200 });
      attempts += 1;
      if (attempts === 1) {
        return new Response('{"detail":"provider mock-a failed"}', { status: 502 });
      }
      return new Response(JSON.stringify(responseBody("recovered")), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      });
    }));
    const user = userEvent.setup();
    const { container } = render(<App />);
    await user.type(await screen.findByRole("textbox", { name: "消息内容" }), "retry me{enter}");
    expect(await screen.findByText("Provider 错误")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "重试" }));
    expect(await screen.findByText("recovered")).toBeInTheDocument();
    expect(container.querySelectorAll(".user-message .message-bubble")).toHaveLength(1);
    expect(container.querySelector(".user-message .message-bubble")).toHaveTextContent("retry me");
  });

  it("aborts an in-flight request and keeps it retryable", async () => {
    vi.stubGlobal("fetch", vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      if (String(input) === "/api/health") return Promise.resolve(new Response("{}", { status: 200 }));
      return new Promise<Response>((_resolve, reject) => {
        init?.signal?.addEventListener("abort", () => reject(new DOMException("aborted", "AbortError")));
      });
    }));
    const user = userEvent.setup();
    render(<App />);
    await user.type(await screen.findByRole("textbox", { name: "消息内容" }), "cancel me{enter}");
    await user.click(await screen.findByRole("button", { name: "取消生成" }));
    expect(await screen.findByText("这次生成已取消。")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "重试" })).toBeInTheDocument();
  });
});
