// Headless smoke for the Ascendo dashboard SPA. Guards the redesign:
//   1. the page boots and the core view renders,
//   2. every window.AC component constructs without throwing (the
//      contract the whole SPA depends on — covers the "AC primitive
//      smoke" goal),
//   3. all 12 IA destinations resolve to a visible view with ZERO
//      console errors, in BOTH themes.
const { test, expect } = require("@playwright/test");

const ROUTES = [
  "#dashboard",
  "#library/sources", "#library/apps", "#library/tools",
  "#runs/start", "#runs/active", "#runs/scheduled", "#runs/history",
  "#insights/trends",
  "#settings/general", "#settings/support", "#settings/about",
];

function trackConsole(page) {
  const errors = [];
  page.on("console", (msg) => { if (msg.type() === "error") errors.push(msg.text()); });
  page.on("pageerror", (err) => errors.push(String(err && err.message ? err.message : err)));
  return errors;
}

test("SPA boots and the Dashboard view renders", async ({ page }) => {
  const errors = trackConsole(page);
  await page.goto("/");
  await expect(page.locator("#view-overview")).toBeVisible();
  expect(errors, "console errors on boot:\n" + errors.join("\n")).toHaveLength(0);
});

test("every window.AC component constructs without throwing", async ({ page }) => {
  await page.goto("/");
  await page.waitForFunction(() => !!window.AC);
  const report = await page.evaluate(() => {
    // opts-taking node primitives, array-taking node primitives, and
    // behaviours (object / drawer-opening) get checked appropriately.
    const NODE_OPTS = ["Card", "StatPair", "StatusPill", "Button", "EmptyState",
      "Banner", "Skeleton", "ProgressBar", "VerdictHeader", "AttentionCard",
      "RunHeader", "SourceProgressRow", "LogViewer", "IntentRunCard",
      "CompletionSummary", "SourceListItem"];
    const NODE_ARR = ["KpiStrip", "Timeline", "AttentionList", "PhaseStepper"];
    const BEHAVIOURS = ["mount", "Drawer", "DangerConfirm"];
    const out = {};
    const isNode = (n) => n === null || n instanceof Node;
    NODE_OPTS.forEach((n) => {
      try { out[n] = isNode(window.AC[n]({})) ? "ok" : "bad-return"; }
      catch (e) { out[n] = "threw: " + e.message; }
    });
    NODE_ARR.forEach((n) => {
      try { out[n] = isNode(window.AC[n]([])) ? "ok" : "bad-return"; }
      catch (e) { out[n] = "threw: " + e.message; }
    });
    BEHAVIOURS.forEach((n) => {
      const v = window.AC[n];
      if (n === "Drawer") out[n] = (v && typeof v.open === "function" && typeof v.close === "function") ? "ok" : "bad";
      else out[n] = (typeof v === "function") ? "ok" : "bad";
    });
    return out;
  });
  for (const [name, status] of Object.entries(report)) {
    expect(status, `AC.${name} → ${status}`).toBe("ok");
  }
});

for (const theme of ["dark", "light"]) {
  test(`all destinations resolve with 0 console errors (${theme})`, async ({ page }) => {
    const errors = trackConsole(page);
    await page.goto("/");
    await page.evaluate((t) => document.documentElement.setAttribute("data-theme", t), theme);
    for (const route of ROUTES) {
      await page.evaluate((r) => { window.location.hash = r; }, route);
      await page.waitForTimeout(300);
      const visibleView = await page.evaluate(() => {
        const v = document.querySelector(".view:not(.hidden)");
        return v ? v.id : null;
      });
      expect(visibleView, `route ${route} → no visible view`).toBeTruthy();
    }
    expect(errors, `console errors (${theme}):\n` + errors.join("\n")).toHaveLength(0);
  });
}
