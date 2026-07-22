import { expect, test } from "@playwright/test";

test.beforeEach(async ({ page }) => {
  let turn = 0;
  await page.route("**/api/health", (route) => route.fulfill({ json: { status: "ok" } }));
  await page.route("**/api/v1/chat/completions", async (route) => {
    turn += 1;
    await route.fulfill({
      json: {
        answer: turn === 1 ? "First **Markdown** answer" : "Second answer",
        polygate: {
          chosen_provider: "mock-a",
          reason: "E2E route",
          cache_hit: false,
          cost_estimate_usd: 0.0001,
          latency_ms: 12,
          tokens: { input: 8, output: 3 },
          retries: 0,
          failover_from: null,
          request_id: `req-${turn}`,
        },
      },
    });
  });
});

test("multi-turn history survives reload", async ({ page }) => {
  await page.goto("/");
  const composer = page.getByRole("textbox", { name: "消息内容" });
  await composer.fill("first");
  await composer.press("Enter");
  await expect(page.getByText("First Markdown answer")).toBeVisible();
  await composer.fill("second");
  const secondRequest = page.waitForRequest("**/api/v1/chat/completions");
  await composer.press("Enter");
  expect((await secondRequest).postDataJSON().messages).toHaveLength(3);
  await expect(page.getByText("Second answer")).toBeVisible();
  await page.reload();
  await expect(page.getByText("First Markdown answer")).toBeVisible();
  await expect(page.getByText("Second answer")).toBeVisible();
});
