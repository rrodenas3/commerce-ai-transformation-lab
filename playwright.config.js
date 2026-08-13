const { defineConfig, devices } = require("@playwright/test");

const origin = "http://127.0.0.1:42973";

module.exports = defineConfig({
  testDir: "./tests/browser",
  fullyParallel: false,
  forbidOnly: true,
  globalSetup: require.resolve("./tests/browser/global-setup"),
  globalTimeout: 120_000,
  retries: 0,
  reporter: "line",
  timeout: 15_000,
  use: {
    baseURL: `${origin}/demo/`,
    trace: "retain-on-failure"
  },
  projects: [
    { name: "chromium", use: { ...devices["Desktop Chrome"] } },
    { name: "mobile-chromium", use: { ...devices["Pixel 7"] } }
  ]
});
