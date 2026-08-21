import { describe, expect, it } from "vitest";
import { adminErrorMessage } from "./admin-errors";

const ADMIN_CODES = [
  "media_rejected",
  "import_not_confirmable",
  "slug_taken",
  "default_preset",
  "last_admin",
  "self_target",
] as const;

describe("adminErrorMessage", () => {
  it.each(ADMIN_CODES)("returns its own sentence for %s, ignoring the fallback", (code) => {
    const message = adminErrorMessage(code, "fallback should not appear");
    expect(message).not.toBe("fallback should not appear");
    expect(message.length).toBeGreaterThan(0);
  });

  it("returns a distinct sentence per code — the map is not six aliases of one string", () => {
    const messages = new Set(ADMIN_CODES.map((code) => adminErrorMessage(code, "fallback")));
    expect(messages.size).toBe(ADMIN_CODES.length);
  });

  it("falls back for a code outside the six this file owns", () => {
    expect(adminErrorMessage("validation_failed", "check the form and try again")).toBe(
      "check the form and try again",
    );
  });
});
