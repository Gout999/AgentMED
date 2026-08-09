import { defineConfig } from "@playwright/test";

export default defineConfig({
  testDir: "./tests/integration",
  outputDir: "../evidence/p0/p0-3-console/playwright",
  fullyParallel: false,
  workers: 1,
  retries: 0,
  timeout: 30_000,
  expect: { timeout: 10_000 },
  reporter: [
    ["line"],
    ["json", { outputFile: "../evidence/p0/p0-3-console/playwright-report.json" }],
  ],
  use: {
    baseURL: "http://127.0.0.1:5173",
    browserName: "chromium",
    headless: true,
    trace: "on",
    screenshot: "only-on-failure",
    video: "off",
  },
});
