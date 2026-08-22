import { defineConfig, devices } from "@playwright/test";

/**
 * Spec 1 §12.4: exactly one scenario, run against the real production
 * compose stack (`./infra/deploy.sh`), never against a dev server this
 * config would have to start itself. `baseURL` is Caddy's one published
 * port (§10.11) — every request in the scenario, browser navigation and
 * the seed's own HTTP calls alike, goes through it, the same origin a real
 * player's browser would use on the LAN.
 *
 * `TRIVIADOR_ALLOWED_ORIGINS` (`.env`) must include this exact origin —
 * every POST the browser makes is checked against it (`OriginMiddleware`),
 * and a websocket handshake's `Origin` header is checked the same way in
 * `api/ws/endpoint.py`.
 */
const BASE_URL = process.env.E2E_BASE_URL ?? "http://localhost";

export default defineConfig({
  testDir: ".",
  testMatch: "*.spec.ts",
  timeout: 180_000,
  expect: { timeout: 10_000 },
  fullyParallel: false,
  workers: 1,
  retries: 0,
  reporter: [["list"]],
  use: {
    baseURL: BASE_URL,
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
    // Every locator action/state-check gets a hard ceiling. Without this,
    // an action's default timeout is 0 (wait forever) — found the hard
    // way in Step 4's proof run: an `isEnabled()` racing a page navigation
    // (the exact FINISHED transition this scenario drives toward) hung
    // for the rest of the test instead of failing fast.
    actionTimeout: 10_000,
  },
  projects: [{ name: "chromium", use: { ...devices["Desktop Chrome"] } }],
});
