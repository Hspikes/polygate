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
});
