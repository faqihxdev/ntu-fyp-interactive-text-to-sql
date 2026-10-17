import { expect, test } from "@playwright/test";
import AxeBuilder from "@axe-core/playwright";

test("same-origin protection works through the dev proxy", async ({
  request,
  baseURL,
}) => {
  const status = await (await request.get("/api/status")).json();
  const validHeaders = { Origin: baseURL!, "X-Explorer-Token": status.token };
  // Invalid payload exercises authentication and validation without creating a branch.
  const allowed = await request.post("/api/branches", {
    headers: validHeaders,
    data: {},
  });
  expect(allowed.status()).toBe(422);
  const foreign = await request.post("/api/branches", {
    headers: { ...validHeaders, Origin: "https://example.com" },
    data: {},
  });
  expect(foreign.status()).toBe(403);
  const missingToken = await request.post("/api/branches", {
    headers: { Origin: baseURL! },
    data: {},
  });
  expect(missingToken.status()).toBe(403);
});

test("dark-only palette, quiet legend and one search focus ring", async ({
  page,
}) => {
  const posts: string[] = [];
  page.on("request", (request) => {
    if (request.method() === "POST") posts.push(request.url());
  });
  await page.emulateMedia({ colorScheme: "light" });
  await page.goto("/");
  await expect(page.locator(".timeline .step")).toHaveCount(4);
  await expect(page.locator("html")).toHaveCSS("color-scheme", "dark");
  await expect(page.locator("body")).toHaveCSS(
    "background-color",
    "rgb(11, 11, 11)",
  );
  await expect(page.locator(".case-outcome-legend")).toHaveCount(0);
  await expect(page.locator(".progress")).toHaveCount(0);
  for (const selector of [
    ".case-button.selected",
    ".method.active",
    ".replay",
    ".sql-frame",
    ".search",
  ]) {
    const element = page.locator(selector).first();
    await expect(element).toHaveCSS("border-top-width", "0px");
    await expect(element).toHaveCSS("outline-style", "none");
    await expect(element).toHaveCSS("box-shadow", "none");
  }
  const selectedMethod = page.locator(".method.active");
  await expect(selectedMethod).toHaveCSS("background-color", "rgb(42, 42, 42)");
  await expect(selectedMethod.locator(".selection-marker")).toBeVisible();
  await page.screenshot({ path: "test-results/neutral-desktop.png" });
  const legend = page.getByLabel("Case result legend", { exact: true });
  const panel = page.locator(".case-legend-panel");
  await expect(panel).not.toBeVisible();
  await expect(legend.locator("svg.lucide-info")).toHaveCount(1);
  await legend.focus();
  await page.keyboard.press("Enter");
  await expect(panel).toBeVisible();
  await expect(panel.locator("li")).toHaveText([
    "Initial-attempt baseline",
    "SQL-only recovery",
    "Reactive clarification",
  ]);
  const audit = await new AxeBuilder({ page })
    .withTags(["wcag2a", "wcag2aa", "wcag21aa"])
    .analyze();
  expect(audit.violations).toEqual([]);
  await page
    .locator(".library")
    .filter({ visible: true })
    .screenshot({ path: "test-results/dark-legend.png" });
  await page.keyboard.press("Escape");
  await expect(panel).not.toBeVisible();
  await expect(legend).toBeFocused();
  await legend.click();
  await page
    .getByRole("heading", { name: "List the powers of Hunter Zolomon." })
    .click();
  await expect(panel).not.toBeVisible();
  const search = page.getByRole("textbox", { name: "Find a question" });
  await search.focus();
  await expect(search).toHaveCSS("outline-style", "none");
  await expect(page.locator(".search")).toHaveCSS("outline-width", "2px");
  await expect(page.locator(".search")).toHaveCSS("outline-style", "solid");
  await page
    .locator(".library-heading")
    .filter({ visible: true })
    .scrollIntoViewIfNeeded();
  await page.screenshot({ path: "test-results/dark-search.png" });
  for (const scheme of ["dark", "light"] as const) {
    await page.emulateMedia({ colorScheme: scheme });
    await expect(page.locator("body")).toHaveCSS(
      "background-color",
      "rgb(11, 11, 11)",
    );
  }
  for (const viewport of [
    { width: 812, height: 375 },
    { width: 320, height: 640 },
  ]) {
    await page.setViewportSize(viewport);
    expect(
      await page.evaluate(
        () => document.documentElement.scrollWidth <= innerWidth,
      ),
    ).toBe(true);
  }
  expect(posts).toEqual([]);
});

test("case previews show each method's saved final outcome", async ({
  page,
}) => {
  await page.goto("/");
  await expect(page.locator(".case-button")).toHaveCount(10);
  const catalogue = await (await page.request.get("/api/catalogue")).json();
  for (const entry of catalogue.cases) {
    const button = page
      .locator(".case-button")
      .filter({ hasText: `#${entry.question_id} ` });
    await expect(button.locator(".case-outcomes > span")).toHaveCount(3);
    for (const [method, matched] of Object.entries(entry.outcomes)) {
      const indicator = button.locator(`[data-method="${method}"]`);
      await expect(indicator).toHaveAttribute("data-match", String(matched));
      await expect(indicator.locator(".sr-only")).toContainText(
        matched ? ": BIRD match" : ": no BIRD match",
      );
    }
  }
  const first = page.locator(".case-button").filter({ hasText: "#1389 " });
  for (const width of [1024, 1280, 1440]) {
    await page.setViewportSize({ width, height: 900 });
    const tops = await first
      .locator(".case-outcomes > span")
      .evaluateAll((items) =>
        items.map((item) => item.getBoundingClientRect().top),
      );
    expect(new Set(tops).size).toBe(1);
    const aligned = await page.locator(".case-meta").evaluateAll((rows) =>
      rows.every((row) => {
        const label = row.querySelector("small")!.getBoundingClientRect();
        const outcomes = row
          .querySelector(".case-outcomes")!
          .getBoundingClientRect();
        return (
          Math.abs(
            label.top + label.height / 2 - outcomes.top - outcomes.height / 2,
          ) < 1
        );
      }),
    );
    expect(aligned, `metadata alignment at ${width}px`).toBe(true);
  }
  await page.getByRole("button", { name: "Previous step" }).click();
  await expect(
    first.locator('[data-method="reactive_clarification"]'),
  ).toHaveAttribute("data-match", "true");
  await first.screenshot({ path: "test-results/case-outcomes.png" });
});

test("icon-only SQL steps and rounded scroll frames remain usable", async ({
  page,
}) => {
  await page.goto("/");
  const noUser = page.getByRole("button", {
    name: "Initial-attempt baseline",
    exact: true,
  });
  await expect(noUser.locator(".mini-flow > span")).toHaveCount(1);
  await expect(noUser.locator(".mini-flow")).toHaveText("");
  await expect(noUser.locator(".mini-flow svg.lucide-x")).toHaveCount(1);
  const retry = page.getByRole("button", {
    name: "SQL-only recovery",
    exact: true,
  });
  await expect(retry.locator(".mini-flow > span")).toHaveCount(5);
  const clarify = page.getByRole("button", {
    name: "Reactive clarification",
    exact: true,
  });
  await expect(
    clarify.locator(".mini-question svg.lucide-message-square"),
  ).toHaveCount(1);
  await expect(clarify.locator(".mini-question")).toHaveAttribute(
    "title",
    "Clarification question",
  );
  await expect(clarify.locator(".mini-match svg.lucide-check")).toHaveCount(1);
  const retryRows = await retry
    .locator(".mini-flow > span")
    .evaluateAll((items) =>
      items.map((item) => item.getBoundingClientRect().top),
    );
  expect(new Set(retryRows).size).toBe(1);
  await page
    .locator(".method-grid")
    .screenshot({ path: "test-results/icon-steps.png" });
  const sql = page.locator("pre.sql").first();
  await expect(page.locator(".sql-frame").first()).toHaveCSS(
    "overflow",
    "hidden",
  );
  await expect(sql).toHaveCSS("overflow", "auto");
  await sql.focus();
  // Deliberately long layout fixture, not saved benchmark SQL.
  await sql.evaluate((element) => {
    element.textContent = "SELECT " + "column_name, ".repeat(300);
  });
  await page.keyboard.press("ArrowRight");
  await expect
    .poll(() => sql.evaluate((element) => element.scrollLeft))
    .toBeGreaterThan(0);
  await page.getByRole("button", { name: "Model input & schema" }).click();
  await page.setViewportSize({ width: 375, height: 500 });
  await expect(page.getByRole("dialog")).toHaveCSS("overflow", "hidden");
  await expect(page.locator(".context-dialog .dialog-content")).toHaveCSS(
    "overflow",
    "auto",
  );
  const raw = page.locator(".context-dialog .raw").first();
  await expect(page.locator(".context-dialog details").first()).toHaveCSS(
    "overflow",
    "hidden",
  );
  await raw.focus();
  await page.keyboard.press("PageDown");
  await expect(raw).toHaveCSS("outline-offset", "-2px");
  await expect
    .poll(() => raw.evaluate((element) => element.scrollTop))
    .toBeGreaterThan(0);
  await page
    .getByRole("dialog")
    .screenshot({ path: "test-results/clipped-dialog.png" });
  await page.keyboard.press("Escape");
  await expect(page.getByRole("dialog")).not.toBeVisible();
});

test("saved playback, method switching, context and results", async ({
  page,
}) => {
  const errors: string[] = [];
  page.on("pageerror", (e) => errors.push(e.message));
  const posts: string[] = [];
  page.on("request", (r) => {
    if (r.method() === "POST") posts.push(r.url());
  });
  await page.goto("/");
  await expect(
    page.getByRole("heading", { name: "List the powers of Hunter Zolomon." }),
  ).toBeVisible();
  await expect(page.locator(".timeline .step")).toHaveCount(4);
  await expect(page.getByRole("button", { name: "Show all" })).toBeDisabled();
  await page.getByRole("button", { name: "Restart replay" }).click();
  await expect(page.locator(".timeline .step")).toHaveCount(1);
  await page.getByRole("button", { name: "Context", exact: true }).click();
  await expect(page.getByText("Saved prompt hash verified")).toBeVisible();
  await page.getByRole("button", { name: "Close context" }).click();
  await page.getByRole("button", { name: "Results", exact: true }).click();
  await expect(page.locator(".preview table")).toBeVisible();
  await page.getByRole("button", { name: "Next", exact: true }).click();
  await expect(
    page.getByRole("heading", { name: "Ask for clarification" }),
  ).toBeVisible();
  await page.getByRole("button", { name: "Show all" }).click();
  await expect(page.locator(".timeline .step")).toHaveCount(4);
  await expect(
    page.getByText("The result matched the BIRD target.", { exact: true }),
  ).toBeVisible();
  await page.screenshot({ path: "test-results/desktop.png", fullPage: true });
  await page
    .getByRole("button", { name: "SQL-only recovery", exact: true })
    .click();
  await expect(page.locator(".timeline .step")).toHaveCount(5);
  await expect(page.getByRole("button", { name: "Show all" })).toBeDisabled();
  await page
    .getByRole("button", { name: "Initial-attempt baseline", exact: true })
    .click();
  await expect(page.locator(".timeline .step")).toHaveCount(1);
  expect(posts).toEqual([]);
  expect(errors).toEqual([]);
});

test("case selection, search and terminal outcomes", async ({ page }) => {
  await page.goto("/");
  await page.getByRole("textbox", { name: "Find a question" }).fill("Copycat");
  await page.getByRole("button", { name: /What is Copycat/ }).click();
  await expect(
    page.getByRole("heading", { name: "What is Copycat's race?" }),
  ).toBeVisible();
  await expect(
    page.getByText("The result matched the BIRD target.", { exact: true }),
  ).toBeVisible();
  await page.getByRole("textbox", { name: "Find a question" }).fill("780");
  await page.locator(".case-button").click();
  const selected = await (await page.request.get("/api/cases/780")).json();
  await expect(page.locator(".timeline .step")).toHaveCount(
    selected.methods.reactive_clarification.steps.length,
  );
  await expect(page.getByRole("button", { name: "Show all" })).toBeDisabled();
  await page
    .getByRole("textbox", { name: "Find a question" })
    .fill("zzzznotacase");
  await expect(
    page.getByText("No matching cases. Try a database name."),
  ).toBeVisible();
});

test("fork is free, API action is explicit, and live result can be read", async ({
  page,
}) => {
  await page.goto("/");
  await page.getByRole("button", { name: "Try your reply" }).click();
  await page.getByText("I may change the intended request").click();
  await page.getByRole("button", { name: "Start a branch · free" }).click();
  await expect(
    page.getByRole("heading", { name: "Continue with your reply" }),
  ).toBeVisible();
  await expect(page.locator(".timeline .step")).toHaveCount(2);
  await expect(
    page.getByRole("button", { name: "Suggest a possible reply · API" }),
  ).toBeVisible();
  await page.getByRole("button", { name: "Use recorded reply · free" }).click();
  await expect(
    page.getByRole("textbox", {
      name: "Your answer to the last clarification",
    }),
  ).not.toHaveValue("");
  let calls = 0;
  await page.route("**/api/branches/*/actions", async (route) => {
    calls++;
    const url = route.request().url().replace("/actions", "");
    const session = await (await page.request.get(url)).json();
    session.steps.push({
      id: 2,
      kind: "answer",
      source: "live",
      role: "visitor",
      output: { answer: "Return one." },
    });
    session.steps.push({
      id: 3,
      kind: "sql",
      source: "live",
      role: "policy",
      call_kind: "live_sql",
      output: { sql: "SELECT 1" },
      candidate: {
        sql: "SELECT 1",
        official_ex: null,
        status: "ok",
        error: null,
        row_count: 1,
      },
      preview: {
        columns: ["1"],
        preview: [[1]],
        status: "ok",
        error: null,
        row_count: 1,
        truncated: false,
      },
    });
    session.phase = "result";
    session.calls = 1;
    session.revision = 1;
    session.answers = 1;
    session.attempts = 2;
    await route.fulfill({ json: { session } });
  });
  await page
    .getByRole("textbox", { name: "Your answer to the last clarification" })
    .fill("Return one.");
  expect(calls).toBe(0);
  await page
    .getByRole("button", { name: "Send reply & generate SQL · API" })
    .click();
  await expect(page.getByText("Not gold-scored")).toBeVisible();
  expect(calls).toBe(1);
  await expect(
    page.getByRole("button", { name: "Ask the next question · API" }),
  ).toBeDisabled();
  await page
    .getByRole("textbox", { name: "What still needs changing?" })
    .fill("I meant two.");
  await expect(
    page.getByRole("button", { name: "Ask the next question · API" }),
  ).toBeEnabled();
  await page.screenshot({
    path: "test-results/live-branch.png",
    fullPage: true,
  });
  const liveAudit = await new AxeBuilder({ page })
    .withTags(["wcag2a", "wcag2aa", "wcag21aa"])
    .analyze();
  expect(liveAudit.violations).toEqual([]);
});

test("375px mobile has no horizontal overflow and dialogs work by keyboard", async ({
  page,
}) => {
  await page.setViewportSize({ width: 375, height: 812 });
  await page.goto("/");
  await expect(
    page.getByRole("heading", { name: "List the powers of Hunter Zolomon." }),
  ).toBeVisible();
  await expect(page.locator(".timeline .step")).toHaveCount(4);
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= window.innerWidth,
    ),
  ).toBe(true);
  await page.screenshot({ path: "test-results/mobile.png", fullPage: true });
  await page.getByRole("button", { name: "Model input & schema" }).click();
  await expect(page.getByRole("dialog")).toBeVisible();
  await page.keyboard.press("Escape");
  await expect(page.getByRole("dialog")).not.toBeVisible();
  await page.getByLabel("Case", { exact: true }).selectOption("737");
  await expect(
    page.getByRole("heading", { name: "What is Copycat's race?" }),
  ).toBeVisible();
});

test("visible page and context dialog pass automated accessibility checks", async ({
  page,
}) => {
  await page.goto("/");
  await expect(page.locator(".timeline .step")).toHaveCount(4);
  const pageAudit = await new AxeBuilder({ page })
    .withTags(["wcag2a", "wcag2aa", "wcag21aa"])
    .analyze();
  expect(pageAudit.violations).toEqual([]);
  await page.getByRole("button", { name: "Model input & schema" }).click();
  const dialogAudit = await new AxeBuilder({ page })
    .withTags(["wcag2a", "wcag2aa", "wcag21aa"])
    .analyze();
  expect(dialogAudit.violations).toEqual([]);
});

test("compact disclosures, consistent gutters and borderless filled controls", async ({
  page,
}) => {
  await page.goto("/");
  await expect(
    page.getByRole("heading", { name: "List the powers of Hunter Zolomon." }),
  ).toBeVisible();
  await expect(page.locator(".case-detail p")).not.toBeVisible();
  await expect(page.locator(".method-guide dl")).not.toBeVisible();
  await page.locator(".case-detail summary").click();
  await expect(page.locator(".case-detail p")).toContainText("full_name");
  await page.locator(".case-detail summary").click();
  await page.locator(".method-guide summary").click();
  await expect(page.locator(".method-guide dl")).toContainText(
    "Retry rejected SQL without asking.",
  );
  await page.locator(".method-guide summary").click();
  await page.screenshot({
    path: "test-results/tailwind-initial.png",
    fullPage: true,
  });
  for (const width of [375, 768, 1280, 1440]) {
    await page.setViewportSize({ width, height: 900 });
    const layout = await page.evaluate(() => {
      const buttons = [...document.querySelectorAll("button")].filter(
        (b) => b.getBoundingClientRect().height > 0,
      );
      return {
        overflow: document.documentElement.scrollWidth > innerWidth,
        undersized: buttons
          .filter((b) => {
            const r = b.getBoundingClientRect();
            return r.height < 43.9 || r.width < 43.9;
          })
          .map((b) => b.textContent),
        ghost: buttons
          .filter((b) => {
            const s = getComputedStyle(b);
            return s.backgroundColor === "rgba(0, 0, 0, 0)";
          })
          .map((b) => b.textContent),
        framed: buttons
          .filter((b) => parseFloat(getComputedStyle(b).borderTopWidth) > 0)
          .map((b) => b.textContent),
        gutter: getComputedStyle(document.querySelector(".workspace")!)
          .columnGap,
      };
    });
    expect(layout.overflow, `overflow at ${width}px`).toBe(false);
    expect(layout.undersized, `small controls at ${width}px`).toEqual([]);
    expect(layout.ghost, `ghost controls at ${width}px`).toEqual([]);
    expect(layout.framed, `outlined controls at ${width}px`).toEqual([]);
    expect(layout.gutter).toBe("24px");
  }
});

test("clarification review is readable without expanding raw prompts", async ({
  page,
}) => {
  await page.goto("/");
  await page.getByRole("button", { name: "Review", exact: true }).click();
  await expect(
    page.getByRole("heading", { name: "Summary & review" }),
  ).toBeVisible();
  await expect(
    page
      .locator(".context-dialog details")
      .filter({ hasText: "Input supplied to the model" })
      .locator("pre"),
  ).not.toBeVisible();
  const audit = await new AxeBuilder({ page })
    .withTags(["wcag2a", "wcag2aa", "wcag21aa"])
    .analyze();
  expect(audit.violations).toEqual([]);
  await page.screenshot({ path: "test-results/tailwind-review.png" });
});
