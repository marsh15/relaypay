import { defineConfig, devices } from "@playwright/test";

export default defineConfig({
  testDir: "./e2e",
  testMatch: "lost-response.spec.ts",
  fullyParallel: false,
  workers: 1,
  timeout: 240_000,
  expect: { timeout: 90_000 },
  reporter: "list",
  outputDir: "../../output/release-video/playwright",
  use: {
    baseURL: process.env.PLAYWRIGHT_BASE_URL ?? "http://127.0.0.1:3000",
    video: { mode: "on", size: { width: 1440, height: 900 } },
    trace: "off",
    screenshot: "off",
    ...devices["Desktop Chrome"],
    viewport: { width: 1440, height: 900 },
  },
});
