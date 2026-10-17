import { defineConfig } from "@playwright/test";
export default defineConfig({
  testDir: "./tests",
  workers: 1,
  timeout: 30000,
  use: {
    baseURL: process.env.DEMO_BASE_URL || "http://127.0.0.1:8765",
    headless: true,
    viewport: { width: 1440, height: 1100 },
  },
  reporter: "list",
  outputDir: "test-results",
});
