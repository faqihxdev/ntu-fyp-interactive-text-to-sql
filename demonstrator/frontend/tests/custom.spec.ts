import { expect, test } from "@playwright/test";
import AxeBuilder from "@axe-core/playwright";
import type { CustomSession } from "../src/types";

// Every paid route is intercepted before the page opens. These tests never use the model.
test.beforeEach(async ({ page }) => {
  await page.route("**/api/branches/*/actions", (route) =>
    route.fulfill({
      status: 502,
      json: { detail: "Test blocked an unexpected paid action" },
    }),
  );
});

test("database descriptions, schema and examples use no paid calls", async ({
  page,
}) => {
  const posts: string[] = [];
  page.on("request", (r) => {
    if (r.method() === "POST") posts.push(r.url());
  });
  await page.goto("/");
  await page
    .getByRole("button", { name: "Try a question", exact: true })
    .click();
  await expect(
    page.getByRole("heading", { name: "Superheroes", exact: true }),
  ).toBeVisible();
  await expect(page.locator(".database-button")).toHaveCount(11);
  await expect(page.locator(".starter-questions button")).toHaveCount(3);
  await page.getByRole("button", { name: "Student club", exact: true }).click();
  await expect(page.locator(".custom-heading")).toContainText(
    "Members, majors",
  );
  const example = page.locator(".starter-questions button").first();
  const text = (await example.innerText()).trim();
  await example.click();
  await expect(page.getByLabel("Or ask your own question")).toHaveValue(text);
  await page.locator(".schema-guide > summary").click();
  await expect(page.getByLabel("Database table definitions")).toContainText(
    "CREATE TABLE",
  );
  await page.getByRole("button", { name: "Saved cases", exact: true }).click();
  await page
    .getByRole("button", { name: "Try a question", exact: true })
    .click();
  await expect(page.getByLabel("Or ask your own question")).toHaveValue(text);
  await page.locator(".schema-guide > summary").click();
  await page.screenshot({
    path: "test-results/custom-desktop.png",
    fullPage: true,
  });
  const audit = await new AxeBuilder({ page })
    .withTags(["wcag2a", "wcag2aa", "wcag21aa"])
    .analyze();
  expect(audit.violations).toEqual([]);
  for (const width of [320, 375, 768, 1024, 1440]) {
    await page.setViewportSize({ width, height: 812 });
    expect(
      await page.evaluate(
        () => document.documentElement.scrollWidth <= innerWidth,
      ),
    ).toBe(true);
  }
  await page.setViewportSize({ width: 375, height: 812 });
  await page.getByLabel("Database", { exact: true }).selectOption("formula_1");
  await expect(
    page.getByRole("heading", { name: "Formula 1", exact: true }),
  ).toBeVisible();
  await page.screenshot({
    path: "test-results/custom-mobile.png",
    fullPage: true,
  });
  expect(posts).toEqual([]);
});

test("custom clarification, reply, unverified result and revision are explicit", async ({
  page,
}) => {
  let calls = 0;
  let session: CustomSession;
  await page.route("**/api/branches/*/actions", async (route) => {
    calls++;
    const body = route.request().postDataJSON();
    if (!session)
      session = await (
        await page.request.get(route.request().url().replace("/actions", ""))
      ).json();
    if (body.action === "generate") {
      session.steps.push({
        id: 0,
        kind: "question",
        role: "policy",
        source: "live",
        output: {
          question: "Which publisher?",
          options: ["Marvel", "DC", "Other"],
        },
      });
      session.phase = "reply";
    } else if (body.action === "stop") session.phase = "done";
    else {
      session.steps.push({
        id: session.steps.length,
        kind: "answer",
        role: "visitor",
        source: "live",
        output: { answer: body.text },
      });
      session.steps.push({
        id: session.steps.length,
        kind: "sql",
        role: "policy",
        source: "live",
        output: { sql: "SELECT 1 AS value" },
        candidate: {
          sql: "SELECT 1 AS value",
          official_ex: null,
          status: "ok",
          error: null,
          row_count: 1,
        },
        preview: {
          columns: ["value"],
          preview: [[1]],
          status: "ok",
          error: null,
          row_count: 1,
          truncated: false,
        },
        context: {
          role: "policy",
          private: false,
          description: "Mock input",
          prompt: "Schema and visitor question",
          instructions: "Read-only",
          output: { sql: "SELECT 1 AS value" },
        },
      });
      session.phase = "result";
      session.attempts++;
    }
    if (body.action !== "stop") session.calls++;
    session.revision++;
    await route.fulfill({ json: { session } });
  });
  await page.goto("/");
  await page
    .getByRole("button", { name: "Try a question", exact: true })
    .click();
  await page.getByLabel("Or ask your own question").fill("Count heroes");
  expect(calls).toBe(0);
  await page.getByRole("button", { name: "Ask AI · API", exact: true }).click();
  await expect(
    page.getByText("Which publisher?", { exact: true }),
  ).toBeVisible();
  expect(calls).toBe(1);
  await page.getByRole("button", { name: "Marvel", exact: true }).click();
  await expect(page.getByLabel("Your clarification answer")).toHaveValue(
    "Marvel",
  );
  expect(calls).toBe(1);
  await page
    .getByRole("button", { name: "Send answer · API", exact: true })
    .click();
  await expect(
    page.getByText("Executed · unverified", { exact: true }),
  ).toBeVisible();
  await expect(page.locator("#custom-main .preview table")).toContainText(
    "value",
  );
  await expect(page.locator("#custom-main")).not.toContainText("BIRD match");
  // Query styling must not clip the whole step when long context is expanded.
  await expect(page.locator("#custom-main article").last()).toHaveCSS(
    "max-height",
    "none",
  );
  await expect(
    page.getByRole("button", { name: /oracle|suggest.*reply/i }),
  ).toHaveCount(0);
  await page.locator("#custom-main .scoring-details > summary").click();
  await expect(page.locator("#custom-main .scoring-details")).toContainText(
    "Schema and visitor question",
  );
  await page.getByRole("button", { name: "Saved cases", exact: true }).click();
  await page
    .getByRole("button", { name: "Try a question", exact: true })
    .click();
  await expect(
    page.getByText("Executed · unverified", { exact: true }),
  ).toBeVisible();
  await page
    .getByLabel("What would you like to change?")
    .fill("Count publishers instead");
  await page
    .getByRole("button", { name: "Request revision · API", exact: true })
    .click();
  await expect(page.locator("#custom-main .sql-repeat")).toBeVisible();
  expect(calls).toBe(3);
  await page.getByRole("button", { name: "Finish", exact: true }).click();
  await expect(page.getByText(/Conversation ended/)).toBeVisible();
  await page.screenshot({
    path: "test-results/custom-conversation.png",
    fullPage: true,
  });
  const audit = await new AxeBuilder({ page })
    .withTags(["wcag2a", "wcag2aa", "wcag21aa"])
    .analyze();
  expect(audit.violations).toEqual([]);
  await page.getByRole("button", { name: "New question", exact: true }).click();
  await expect(page.getByLabel("Or ask your own question")).toBeVisible();
  await expect(
    page.getByRole("button", { name: "Student club", exact: true }),
  ).toBeEnabled();
});

test("waiting controls keep equal side and bottom insets", async ({ page }) => {
  let release!: () => void;
  const waiting = new Promise<void>((resolve) => {
    release = resolve;
  });
  await page.route("**/api/branches/*/actions", async (route) => {
    await waiting;
    await route.fulfill({
      status: 502,
      json: { detail: "Mock call ended. No API request was made." },
    });
  });
  await page.goto("/");
  await page
    .getByRole("button", { name: "Try a question", exact: true })
    .click();
  await page.getByLabel("Or ask your own question").fill("Count heroes");
  await page.getByRole("button", { name: "Ask AI · API", exact: true }).click();
  await expect(page.getByRole("status")).toContainText("Waiting for the API");
  for (const width of [1440, 375]) {
    await page.setViewportSize({ width, height: 900 });
    const outer = await page.locator(".custom-conversation").boundingBox();
    const inner = await page
      .locator("#custom-main .live-controls")
      .boundingBox();
    const inset = width >= 640 ? 20 : 12;
    expect(inner!.x - outer!.x).toBeCloseTo(inset, 0);
    expect(outer!.y + outer!.height - inner!.y - inner!.height).toBeCloseTo(
      inset,
      0,
    );
  }
  await page.setViewportSize({ width: 1440, height: 900 });
  await page
    .locator(".custom-conversation")
    .screenshot({ path: "test-results/custom-padding.png" });
  release();
  await expect(page.getByRole("alert")).toContainText("Mock call ended");
});

test("database checks pause for Continue, retain context and handle failed calls", async ({
  page,
}) => {
  let calls = 0;
  let session: CustomSession;
  const sent: string[] = [];
  await page.route("**/api/branches/*", async (route) => {
    if (session) await route.fulfill({ json: session });
    else await route.continue();
  });
  await page.route("**/api/branches/*/actions", async (route) => {
    const body = route.request().postDataJSON();
    calls++;
    if (!session)
      session = await (
        await page.request.get(route.request().url().replace("/actions", ""))
      ).json();
    if (calls === 2) {
      session.calls++;
      await route.fulfill({
        status: 502,
        json: { detail: "Test API failure. Retry explicitly." },
      });
      return;
    }
    const inspect = body.action === "generate";
    sent.push(body.text);
    session.steps.push({
      id: session.steps.length,
      kind: "sql",
      role: "policy",
      source: "live",
      query_purpose: inspect ? "inspect" : "answer",
      call_kind: "custom_sql",
      output: {
        sql: "SELECT publisher_name FROM publisher LIMIT 3",
        purpose: "Check the stored publisher names.",
      },
      candidate: {
        sql: "SELECT publisher_name FROM publisher LIMIT 3",
        official_ex: null,
        status: "ok",
        error: null,
        row_count: 25,
      },
      preview: {
        columns: ["publisher_name"],
        preview: [["Marvel Comics"], ["DC Comics"]],
        row_count: 25,
        status: "ok",
        error: null,
        truncated: false,
        preview_limited: true,
      },
    });
    session.phase = inspect ? "inspect" : "result";
    session.calls++;
    session.attempts++;
    session.revision++;
    await route.fulfill({ json: { session } });
  });
  await page.goto("/");
  await page
    .getByRole("button", { name: "Try a question", exact: true })
    .click();
  await page
    .getByLabel("Or ask your own question")
    .fill("Which publishers are represented?");
  await page.getByRole("button", { name: "Ask AI · API", exact: true }).click();
  await expect(
    page.getByRole("heading", { name: "Database check", exact: true }),
  ).toBeVisible();
  await expect(page.getByText("Observation", { exact: true })).toBeVisible();
  await expect(page.getByText("Marvel Comics", { exact: true })).toBeVisible();
  await expect(
    page.getByText("Executed · unverified", { exact: true }),
  ).toHaveCount(0);
  await expect(page.getByText(/Preview shortened/)).toBeVisible();
  expect(calls).toBe(1);
  await page.screenshot({
    path: "test-results/custom-inspect-desktop.png",
    fullPage: true,
  });
  const audit = await new AxeBuilder({ page })
    .withTags(["wcag2a", "wcag2aa", "wcag21aa"])
    .analyze();
  expect(audit.violations).toEqual([]);
  await page.setViewportSize({ width: 375, height: 900 });
  await page.locator("#custom-main .live-controls").scrollIntoViewIfNeeded();
  await page.screenshot({
    path: "test-results/custom-inspect-mobile.png",
    fullPage: true,
  });
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
  ).toBe(true);
  await page.getByText("Add context", { exact: true }).click();
  await page
    .getByLabel("Anything else the AI should know?")
    .fill("Include publishers with zero heroes");
  expect(calls).toBe(1);
  await page
    .getByRole("button", { name: "Continue · API", exact: true })
    .click();
  await expect(page.getByRole("alert")).toContainText("Test API failure");
  await expect(
    page.getByLabel("Anything else the AI should know?"),
  ).toHaveValue("Include publishers with zero heroes");
  await expect(
    page.getByRole("button", { name: "Continue · API", exact: true }),
  ).toBeEnabled();
  await page
    .getByRole("button", { name: "Continue · API", exact: true })
    .click();
  await expect(
    page.getByRole("heading", { name: "Answer query", exact: true }),
  ).toBeVisible();
  await expect(
    page.getByText("Executed · unverified", { exact: true }),
  ).toBeVisible();
  expect(sent.at(-1)).toBe("Include publishers with zero heroes");
  expect(calls).toBe(3);
});

test("exhausted custom sessions offer a fresh question instead of more paid calls", async ({
  page,
}) => {
  await page.route("**/api/branches/*/actions", async (route) => {
    const session: CustomSession = await (
      await page.request.get(route.request().url().replace("/actions", ""))
    ).json();
    session.calls = 12;
    session.phase = "done";
    session.termination = "call_limit";
    await route.fulfill({ json: { session } });
  });
  await page.goto("/");
  await page
    .getByRole("button", { name: "Try a question", exact: true })
    .click();
  await page.getByLabel("Or ask your own question").fill("Count heroes");
  await page.getByRole("button", { name: "Ask AI · API", exact: true }).click();
  await expect(page.getByRole("status")).toContainText("12-call limit");
  await expect(page.locator("#custom-main .live-controls")).toHaveCount(0);
  await expect(
    page.getByRole("button", { name: "New question", exact: true }),
  ).toBeEnabled();
});

test("study totals, score boundaries and SQL changes match the report", async ({
  page,
}) => {
  await page.goto("/");
  await page
    .getByText("Study results & information flow", { exact: true })
    .click();
  await expect(page.locator(".bar-label strong")).toHaveText([
    "43.16%",
    "50.13%",
    "77.51%",
  ]);
  await expect(page.locator(".study-takeaway")).toContainText(
    "27.38 percentage points",
  );
  await expect(page.locator(".study-content")).toContainText(
    "retry recovers 107 and clarification recovers 527",
  );
  await expect(page.locator(".study-content")).toContainText(
    "Sphinteract already compares clarification with retry",
  );
  await page.locator(".sql-change summary").click();
  await expect(page.locator(".sql-change .added")).toContainText("full_name");
  await page.locator(".timeline .scoring-details summary").first().click();
  await expect(
    page.locator(".timeline .scoring-details").first(),
  ).toContainText("Sphinteract-compatible");
  await page
    .getByRole("button", { name: "SQL-only recovery", exact: true })
    .click();
  await expect(page.locator(".sql-repeat")).toHaveCount(3);
  await expect(page.locator(".sql-change")).toHaveCount(1);
  const audit = await new AxeBuilder({ page })
    .withTags(["wcag2a", "wcag2aa", "wcag21aa"])
    .analyze();
  expect(audit.violations).toEqual([]);
});
