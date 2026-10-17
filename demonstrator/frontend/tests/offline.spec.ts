import { expect, test } from "@playwright/test";
import AxeBuilder from "@axe-core/playwright";

test("saved case and cached results need no live inputs", async ({ page }) => {
  await page.route("**/api/status", (route) => route.fulfill({
    json: { token: "test", live: { available: false, branch_inputs_ready: false, databases_ready: false } },
  }));
  const posts: string[] = [];
  page.on("request", (request) => {
    if (request.method() === "POST") posts.push(request.url());
  });
  await page.goto("/");
  await expect(page.getByRole("heading", { name: "List the powers of Hunter Zolomon." })).toBeVisible();
  await expect(page.locator(".timeline .step")).toHaveCount(4);
  expect(posts).toEqual([]);
});

test("missing databases show setup instructions without a failing detail request", async ({ page }) => {
  await page.route("**/api/databases", (route) => route.fulfill({ json: [] }));
  const details: string[] = [];
  page.on("request", (request) => {
    if (request.url().includes("/api/databases/")) details.push(request.url());
  });
  await page.goto("/");
  await page.getByRole("button", { name: "Try a question", exact: true }).click();
  await expect(page.getByRole("heading", { name: "Set up live questions" })).toBeVisible();
  await expect(page.locator("#custom-main pre")).toContainText("fetch_public_inputs demo");
  await expect(page.locator("#custom-main")).toContainText("then restart the demo");
  expect(details).toEqual([]);
  for (const width of [375, 1440]) {
    await page.setViewportSize({ width, height: 900 });
    expect(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth)).toBe(false);
  }
  expect((await new AxeBuilder({ page }).withTags(["wcag2a", "wcag2aa", "wcag21aa"]).analyze()).violations).toEqual([]);
});
