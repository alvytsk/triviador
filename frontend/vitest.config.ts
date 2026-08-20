import { resolve } from "node:path";
import { fileURLToPath } from "node:url";
import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";

const here = fileURLToPath(new URL(".", import.meta.url));

export default defineConfig({
  plugins: [react()],
  resolve: { alias: { "@": resolve(here, "src") } },
  test: {
    globals: true,
    environment: "jsdom",
    setupFiles: ["./testing/setup.ts"],
    // A test that leaks a timer, a socket or an MSW handler into the next
    // test is the failure mode this whole suite is most exposed to, because
    // almost everything here is stateful. Isolate rather than debug it later.
    restoreMocks: true,
    clearMocks: true,
  },
});
