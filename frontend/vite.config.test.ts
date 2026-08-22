// vite.config.ts computes `here`/`MAPS_ROOT` from `import.meta.url`. Under
// the default jsdom environment, Vite's browser transform rewrites
// `import.meta.url` to a `http://localhost:3000/@fs/...` dev-server URL
// (jsdom's default document origin) instead of leaving it a real `file:`
// URL, so `fileURLToPath(new URL(".", import.meta.url))` throws before this
// file's own assertions ever run. Node environment doesn't apply that
// rewrite, so the import resolves the same way it does for `vite`/`pnpm
// dev` themselves.
// @vitest-environment node
import { describe, expect, it, vi } from "vitest";

// Importing the config module executes defineConfig, so the proxy table is
// inspectable without starting a server.
import config from "./vite.config";

// Asserts the DEFAULTS, deliberately. The shipped bug is in the default
// (`/media` -> :8000), so a test that sets VITE_MEDIA_TARGET first would
// assert its own fixture and pass against the broken code. A test that only
// passes under a particular shell environment also fails in CI for reasons
// unrelated to the code.
function proxy() {
  const resolved =
    typeof config === "function" ? config({ command: "serve", mode: "development" }) : config;
  const table = (resolved as { server?: { proxy?: Record<string, unknown> } }).server?.proxy;
  if (!table) throw new Error("no server.proxy in vite config");
  return table;
}

describe("the dev proxy", () => {
  it("sends /media to Garage's web endpoint, never to the API", () => {
    const media = proxy()["/media"] as { target: string };
    // The bug this test exists to prevent: /media pointed at :8000, where
    // nothing serves media, so every question image 404d in development.
    expect(media.target).not.toContain("8000");
    expect(media.target).toBe("http://127.0.0.1:3902");
  });

  it("rewrites Host for /media so Garage can resolve the bucket", () => {
    // Garage resolves a bucket from the Host header against
    // root_domain = ".web.garage.internal". Forwarding the browser's Host
    // (localhost:5173) means no bucket matches and every image 404s.
    const media = proxy()["/media"] as { headers?: Record<string, string> };
    expect(media.headers?.Host).toBe("triviador-media.web.garage.internal");
  });

  it("honours a compose-network override", async () => {
    // The dev overlay sets these to service names; the default is the
    // host-side port for `pnpm dev` outside compose. Both must work.
    //
    // A cache-busting `import("./vite.config?" + query)` (the brief's first
    // choice) hits Vite's SSR module runner with "Unknown variable dynamic
    // import" for a template-literal specifier under the `node` test
    // environment this file needs (see the top-of-file note) — so this
    // resets the module cache and re-imports the plain specifier instead,
    // exactly as the brief's fallback allows.
    vi.stubEnv("VITE_MEDIA_TARGET", "http://garage:3902");
    vi.resetModules();
    const fresh = await import("./vite.config");
    const resolved =
      typeof fresh.default === "function"
        ? fresh.default({ command: "serve", mode: "development" })
        : fresh.default;
    expect(resolved.server.proxy["/media"].target).toBe("http://garage:3902");
    vi.unstubAllEnvs();
  });

  it("strips the /media prefix, the same way Caddy's handle_path does in production", () => {
    // The bug this test exists to prevent: Caddy's production `/media/*`
    // handler strips the matched prefix before proxying
    // (`strip_path_prefix`); this dev proxy entry did not, so an upstream
    // received `/media/ab/abcdef.webp` verbatim and every question image
    // 404d in development.
    const media = proxy()["/media"] as { rewrite?: (path: string) => string };
    if (!media.rewrite) throw new Error("no rewrite on the /media proxy entry");
    expect(media.rewrite("/media/ab/x.webp")).toBe("/ab/x.webp");
  });

  it("leaves the browser's Origin intact for /api and /ws", () => {
    // §6.4 checks Origin exactly; rewriting it would make development pass
    // a check production performs differently.
    const table = proxy();
    expect((table["/api"] as { changeOrigin: boolean }).changeOrigin).toBe(false);
    expect((table["/ws"] as { changeOrigin: boolean }).changeOrigin).toBe(false);
  });
});
