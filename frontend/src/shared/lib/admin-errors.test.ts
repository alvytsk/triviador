import { describe, expect, it } from "vitest";
import { adminErrorMessage } from "./admin-errors";

const ADMIN_CODES = [
  "media_rejected",
  "import_not_confirmable",
  "slug_taken",
  "last_admin",
  "self_target",
] as const;

describe("adminErrorMessage", () => {
  it.each(ADMIN_CODES)("returns its own sentence for %s, ignoring the fallback", (code) => {
    const message = adminErrorMessage(code, "fallback should not appear");
    expect(message).not.toBe("fallback should not appear");
    expect(message.length).toBeGreaterThan(0);
  });

  it("returns a distinct sentence per code — the map is not five aliases of one string", () => {
    const messages = new Set(ADMIN_CODES.map((code) => adminErrorMessage(code, "fallback")));
    expect(messages.size).toBe(ADMIN_CODES.length);
  });

  it("falls back for a code outside the five this file owns", () => {
    expect(adminErrorMessage("validation_failed", "check the form and try again")).toBe(
      "check the form and try again",
    );
  });

  it("falls back for default_preset instead of a fixed sentence, because its message differs by refusal", () => {
    // Task 8's presets screen hits this code from two different refusals
    // with two different server messages (clearing the default; promoting
    // a retired preset to default). A fixed sentence here could only be
    // right for one of them — so, unlike the five above, this code passes
    // the caller's own `fallback` straight through, both times.
    const clearingDefault = adminErrorMessage(
      "default_preset",
      "this is the default preset; make another one default instead of clearing this one",
    );
    const promotingRetired = adminErrorMessage(
      "default_preset",
      "a retired preset cannot be the default; reactivate it first",
    );
    expect(clearingDefault).toBe(
      "this is the default preset; make another one default instead of clearing this one",
    );
    expect(promotingRetired).toBe("a retired preset cannot be the default; reactivate it first");
    expect(clearingDefault).not.toBe(promotingRetired);
  });
});
