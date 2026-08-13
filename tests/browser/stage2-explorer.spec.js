const { test, expect } = require("@playwright/test");

const externalRequests = (page, testInfo) => {
  const allowedOrigin = new URL(testInfo.project.use.baseURL).origin;
  const unexpected = [];
  page.on("request", (request) => {
    const url = new URL(request.url());
    if (url.origin !== allowedOrigin) unexpected.push(request.url());
  });
  return unexpected;
};

test("opens with the decision and claim boundary before implementation detail", async ({ page }, testInfo) => {
  const unexpected = externalRequests(page, testInfo);
  const response = await page.goto("./");
  const headers = await response.allHeaders();
  expect(headers["x-commerce-lab-test-server"]).toBe("stage2-evidence-explorer-v1");
  await expect(page.locator("[data-app-state]")).toHaveAttribute("data-app-state", "ready");
  await expect(page.getByRole("heading", { level: 1 })).toContainText("Delayed or partial fulfilment");
  await expect(page.getByTestId("decision-word")).toHaveText("PAUSE");
  await expect(page.locator("#execution-commit")).toHaveText("83.33%");
  await expect(page.locator("#pending-count")).toHaveText("3 pending");
  await expect(page.locator("#human-evidence")).toHaveText(/Human evidence: Not observed/i);
  await expect(page.getByRole("link", { name: "Skip to decision" })).toBeAttached();
  expect(unexpected).toEqual([]);
});

test("social metadata matches the committed preview asset", async ({ page }) => {
  await page.goto("./");
  const declared = await page.locator("meta[property='og:image']").getAttribute("content");
  expect(declared).toBe("social-preview.png");
  await expect(page.locator("meta[property='og:image:alt']")).toHaveAttribute("content", /PAUSE.*83\.33%.*three pending/i);
  const dimensions = await page.evaluate(async () => {
    const response = await fetch("social-preview.png");
    const bytes = new Uint8Array(await response.arrayBuffer());
    const view = new DataView(bytes.buffer);
    return { width: view.getUint32(16), height: view.getUint32(20) };
  });
  expect(dimensions).toEqual({ width: 1730, height: 909 });
  await expect(page.locator("meta[property='og:image:width']")).toHaveAttribute("content", String(dimensions.width));
  await expect(page.locator("meta[property='og:image:height']")).toHaveAttribute("content", String(dimensions.height));
});

test("conserves the full denominator and explains a pending case", async ({ page }) => {
  await page.goto("./");
  await expect(page.locator("[data-case-id]")).toHaveCount(36);
  await page.getByLabel("Outcome filter").selectOption("pending");
  await expect(page.locator("[data-case-id]:visible")).toHaveCount(3);
  await expect(page.getByTestId("selected-case-title")).toContainText("S2-CASE-5022");
  await expect(page.locator("[data-case-id]:visible button[aria-current='true']")).toHaveCount(1);
  await expect(page).toHaveURL(/case=S2-CASE-5022/);
  await page.getByRole("button", { name: /S2-CASE-5022/ }).click();
  await expect(page.getByTestId("selected-case-title")).toContainText("S2-CASE-5022");
  await expect(page.getByTestId("selected-case-outcome")).toContainText("Pending");
  await expect(page.getByTestId("case-limitation")).toContainText(/authoritative postcondition|pending/i);
  await expect(page.getByTestId("case-evidence-chain").locator("li")).not.toHaveCount(0);
});

test("provides clear no-match and invalid-selection recovery", async ({ page }) => {
  await page.goto("./?case=S2-CASE-NOT-REAL");
  await expect(page.getByRole("status")).toContainText("could not be found");
  await page.getByLabel("Find a case").fill("does-not-exist");
  await expect(page.getByTestId("case-empty-state")).toBeVisible();
  await expect(page.getByTestId("case-empty-state")).toContainText("No cases match");
  await page.getByTestId("case-empty-state").getByRole("button", { name: "Clear case filters" }).click();
  await expect(page.locator("[data-case-id]:visible")).toHaveCount(36);
});

test("fails safely when the evidence projection cannot be parsed", async ({ page }) => {
  await page.route("**/data/evidence-pack.json", (route) =>
    route.fulfill({ status: 200, contentType: "application/json", body: "{not-json" })
  );
  await page.goto("./");
  await expect(page.locator("[data-app-state]")).toHaveAttribute("data-app-state", "error");
  await expect(page.getByRole("alert")).toContainText("Evidence could not be opened");
  await expect(page.getByRole("alert")).toContainText("Inspect the raw public projection");
  await expect(page.locator("[data-verified-decision]")).toBeHidden();
});

test("fails closed when projection text contains unsafe controls", async ({ page }) => {
  await page.route("**/data/evidence-pack.json", async (route) => {
    const response = await route.fetch();
    const payload = await response.json();
    payload.claim_boundary.must_not_say = "trusted\u202efalse";
    await route.fulfill({ response, json: payload });
  });
  await page.goto("./");
  await expect(page.locator("[data-app-state]")).toHaveAttribute("data-app-state", "error");
  await expect(page.getByRole("alert")).toContainText("unsafe control characters");
  await expect(page.locator("[data-verified-decision]")).toBeHidden();
});

test("rejects altered claims, boundaries, and contradictory outcome counts", async ({ page }) => {
  const mutations = [
    (payload) => { payload.evidence_boundary.human_evidence = "observed"; },
    (payload) => { payload.decision.authorises_company_pilot = true; },
    (payload) => {
      payload.outcomes.counts.pending = 2;
      payload.outcomes.counts.verified_remedy = 16;
    },
    (payload) => { payload.metrics.find((item) => item.metric_id === "execution_commit").value = 10000; }
  ];
  for (const mutate of mutations) {
    await page.unrouteAll({ behavior: "ignoreErrors" });
    await page.route("**/data/evidence-pack.json", async (route) => {
      const response = await route.fetch();
      const payload = await response.json();
      mutate(payload);
      await route.fulfill({ response, json: payload });
    });
    await page.goto("./");
    await expect(page.locator("[data-app-state]")).toHaveAttribute("data-app-state", "error");
    await expect(page.locator("[data-verified-decision]")).toBeHidden();
  }
});

test("keeps evidence links local, descriptive, and keyboard reachable", async ({ page }) => {
  await page.goto("./");
  for (const link of await page.locator("a[href]").all()) {
    const href = await link.getAttribute("href");
    expect(href).not.toMatch(/^(?:javascript|data):/i);
    expect(href).not.toMatch(/^https?:\/\//i);
    await expect(link).not.toHaveText(/^(?:click here|read more)$/i);
  }
  await page.keyboard.press("Tab");
  await expect(page.getByRole("link", { name: "Skip to decision" })).toBeFocused();
  await expect(page.locator("table caption")).toHaveCount(2);
});

test("adapts at a narrow viewport and respects reduced motion", async ({ page }) => {
  await page.emulateMedia({ reducedMotion: "reduce" });
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto("./");
  await expect(page.getByRole("heading", { level: 1 })).toBeVisible();
  await expect(page.getByTestId("case-index")).toBeVisible();
  await expect(page.getByTestId("selected-case-panel")).toBeVisible();
  expect(await page.evaluate(() => getComputedStyle(document.documentElement).scrollBehavior)).toBe("auto");
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= document.documentElement.clientWidth)).toBe(true);
});
