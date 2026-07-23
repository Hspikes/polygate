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

test("composer stays docked while routing details expand over the message area", async ({ page }, testInfo) => {
  const touchLayout = testInfo.project.name === "mobile";
  await page.goto("/");
  const composer = page.getByRole("textbox", { name: "消息内容" });

  for (let turn = 1; turn <= 6; turn += 1) {
    await composer.fill(`turn ${turn}`);
    await composer.press("Enter");
    await expect(page.locator(".response-card")).toHaveCount(turn);
  }

  const viewport = page.viewportSize();
  const dock = await page.locator(".composer-area").boundingBox();
  expect(viewport).not.toBeNull();
  expect(dock).not.toBeNull();
  expect(Math.abs(dock!.y + dock!.height - viewport!.height)).toBeLessThanOrEqual(1);
  await expect(composer).toBeVisible();
  await expect.poll(async () => {
    const lastMessage = await page.locator(".message-row").last().boundingBox();
    return lastMessage ? lastMessage.y + lastMessage.height : Number.POSITIVE_INFINITY;
  }).toBeLessThanOrEqual(dock!.y + 1);
  expect(await page.evaluate(() => document.scrollingElement?.scrollTop ?? 0)).toBe(0);

  const decisionCard = page.locator(".decision-card").last();
  const decisionBody = decisionCard.locator(".decision-body-collapsible");
  expect((await decisionBody.boundingBox())?.height ?? 0).toBeLessThanOrEqual(1);
  const decisionTrigger = decisionCard.getByRole("button", { name: /路由策略/ });
  if (touchLayout) await decisionTrigger.click();
  else await decisionTrigger.hover();
  await expect.poll(async () => (await decisionBody.boundingBox())?.height ?? 0).toBeGreaterThan(40);
  if (touchLayout) await decisionTrigger.click();
  else await composer.hover();
  await expect.poll(async () => (await decisionBody.boundingBox())?.height ?? 0).toBeLessThanOrEqual(1);

  const routingPanel = page.locator(".routing-panel");
  const routingTrigger = routingPanel.getByRole("button", { name: /路由偏好/ });
  const routingContent = routingPanel.locator(".routing-panel-content");
  if (touchLayout) await routingTrigger.click();
  else await routingTrigger.hover();
  await expect(routingTrigger).toHaveAttribute("aria-expanded", "true");
  await expect.poll(async () => (await routingContent.boundingBox())?.height ?? 0).toBeGreaterThan(40);
  const expandedDock = await page.locator(".composer-area").boundingBox();
  expect(Math.abs(expandedDock!.y + expandedDock!.height - viewport!.height)).toBeLessThanOrEqual(1);

  await routingPanel.getByRole("combobox", { name: "质量" }).selectOption("high");
  if (touchLayout) await routingTrigger.click();
  else await composer.hover();
  await expect(routingTrigger).toHaveAttribute("aria-expanded", "false");
  await expect.poll(async () => (await routingContent.boundingBox())?.height ?? 0).toBeLessThanOrEqual(1);
});
