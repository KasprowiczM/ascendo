// Playwright config for the Ascendo dashboard smoke suite.
// Starts the real FastAPI dashboard as the test web server (foreground
// `ascendo dashboard`), then drives the SPA in headless Chromium. CI sets
// PYTHONPATH=core:adapters/<os> so the dashboard resolves a real adapter.
const { defineConfig, devices } = require("@playwright/test");

module.exports = defineConfig({
  testDir: ".",
  timeout: 30000,
  expect: { timeout: 10000 },
  retries: 0,
  reporter: [["list"]],
  use: {
    baseURL: "http://127.0.0.1:8765",
    headless: true,
    viewport: { width: 1366, height: 900 },
  },
  projects: [{ name: "chromium", use: { ...devices["Desktop Chrome"] } }],
  webServer: {
    command: "python -m ascendo dashboard --host 127.0.0.1 --port 8765",
    url: "http://127.0.0.1:8765/health",
    timeout: 60000,
    reuseExistingServer: true,
  },
});
