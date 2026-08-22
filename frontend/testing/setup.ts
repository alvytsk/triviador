import "@testing-library/jest-dom/vitest";
import { cleanup } from "@testing-library/react";
import { afterAll, afterEach, beforeAll } from "vitest";
import { server } from "./msw";

/**
 * jsdom implements none of these — Radix's `Select` (Task 3, the first
 * Radix primitive this codebase vendors) calls all three while opening
 * and positioning its popper-mode content, and throws without them. Real
 * browsers have always had them; this is a test-environment gap, not a
 * behavior the app relies on, so a no-op stand-in is correct rather than
 * a workaround.
 *
 * Guarded on `typeof Element` because this file runs for every test
 * (`vitest.config.ts`'s `setupFiles` is global), and `vite.config.test.ts`
 * opts into `@vitest-environment node` — no `Element` there, no DOM
 * behavior to patch, and no jsdom tests lose the patch either way.
 */
if (typeof Element !== "undefined") {
  if (!Element.prototype.hasPointerCapture) {
    Element.prototype.hasPointerCapture = () => false;
  }
  if (!Element.prototype.releasePointerCapture) {
    Element.prototype.releasePointerCapture = () => {};
  }
  if (!Element.prototype.scrollIntoView) {
    Element.prototype.scrollIntoView = () => {};
  }
}
if (typeof globalThis.ResizeObserver === "undefined") {
  class ResizeObserverStub {
    observe() {}
    unobserve() {}
    disconnect() {}
  }
  globalThis.ResizeObserver = ResizeObserverStub as unknown as typeof ResizeObserver;
}
/**
 * jsdom 30 does not implement `URL.createObjectURL`/`revokeObjectURL` at
 * all — not even as a throwing stub (confirmed: the property is simply
 * absent). The import wizard's "download the rejected rows" feature
 * (`use-import-flow.ts`) needs both to hand the browser a file, and real
 * browsers have always had them — a test-environment gap, not a behavior
 * the app relies on, exactly like the three stand-ins above.
 */
if (typeof URL.createObjectURL !== "function") {
  URL.createObjectURL = () => "blob:mock";
}
if (typeof URL.revokeObjectURL !== "function") {
  URL.revokeObjectURL = () => {};
}

beforeAll(() => {
  server.listen({ onUnhandledRequest: "error" });
});

afterEach(() => {
  cleanup();
  server.resetHandlers();
});

afterAll(() => {
  server.close();
});
