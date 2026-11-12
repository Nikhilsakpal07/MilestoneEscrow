import { defineConfig, devices } from "@playwright/test"

/**
 * Enterprise Playwright Configuration for MilestoneEscrow E2E Testing Suite.
 * Incorporates custom reporting, network interception fixtures, and headless Chromium emulation.
 */
export default defineConfig({
  testDir: "./tests",
  timeout: 60 * 1000,
  expect: {
    timeout: 10 * 1000,
  },
  fullyParallel: false,
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 2 : 0,
  workers: 1,

  // Custom QA Reporter generates QA_Report.md and QA_Report.html automatically
  reporter: [
    ["list"],
    ["./qa-reporter.ts"],
  ],

  use: {
    baseURL: process.env.BASE_URL || "http://localhost:3010",
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
    video: "retain-on-failure",
    viewport: { width: 1440, height: 900 },
    permissions: ["clipboard-read", "clipboard-write"],
    actionTimeout: 15 * 1000,
    navigationTimeout: 30 * 1000,
  },

  projects: [
    {
      name: "Desktop Chromium",
      use: {
        ...devices["Desktop Chrome"],
        viewport: { width: 1440, height: 900 },
      },
    },
  ],

  // Launch Next.js dev server if not already active
  webServer: {
    command: "npx next dev -p 3010",
    url: "http://localhost:3010",
    reuseExistingServer: !process.env.CI,
    timeout: 120 * 1000,
  },
})

