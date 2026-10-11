import { defineConfig } from "@playwright/test";
export default defineConfig({
  testDir: "./tests",
  testMatch: "auth.spec.ts",
  workers: 1,
  webServer: { command: "npm run preview -- --port 4174", url: "http://127.0.0.1:4174", reuseExistingServer: false },
  use: { baseURL: "http://127.0.0.1:4174", viewport: { width: 1440, height: 1000 }, screenshot: "only-on-failure" },
  reporter: "list",
});
