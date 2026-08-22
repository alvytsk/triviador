import { describe, expect, it } from "vitest";
import { adminErrorMessage } from "./admin-errors";

const ADMIN_CODES = ["slug_taken", "last_admin", "self_target"] as const;

describe("adminErrorMessage", () => {
  it.each(ADMIN_CODES)("returns its own sentence for %s, ignoring the fallback", (code) => {
    const message = adminErrorMessage(code, "fallback should not appear");
    expect(message).not.toBe("fallback should not appear");
    expect(message.length).toBeGreaterThan(0);
  });

  it("returns a distinct sentence per code — the map is not three aliases of one string", () => {
    const messages = new Set(ADMIN_CODES.map((code) => adminErrorMessage(code, "fallback")));
    expect(messages.size).toBe(ADMIN_CODES.length);
  });

  it("falls back for a code outside the three this file owns", () => {
    expect(adminErrorMessage("validation_failed", "check the form and try again")).toBe(
      "check the form and try again",
    );
  });

  it("falls back for default_preset instead of a fixed sentence, because its message differs by refusal", () => {
    // The admin presets screen hits this code from THREE different
    // refusals with three different server messages (clearing the
    // default via PATCH; promoting a retired preset to default via
    // PATCH; retiring the default via DELETE). A fixed sentence here
    // could only be right for one of them — so this code passes the
    // caller's own `fallback` straight through, every time.
    const clearingDefault = adminErrorMessage(
      "default_preset",
      "this is the default preset; make another one default instead of clearing this one",
    );
    const promotingRetired = adminErrorMessage(
      "default_preset",
      "a retired preset cannot be the default; reactivate it first",
    );
    const retiringDefault = adminErrorMessage(
      "default_preset",
      "this is the default preset; make another one default first",
    );
    expect(clearingDefault).toBe(
      "this is the default preset; make another one default instead of clearing this one",
    );
    expect(promotingRetired).toBe("a retired preset cannot be the default; reactivate it first");
    expect(retiringDefault).toBe("this is the default preset; make another one default first");
    expect(new Set([clearingDefault, promotingRetired, retiringDefault]).size).toBe(3);
  });

  it("falls back for media_rejected, because it has 6 distinct raise sites with 6 distinct messages", () => {
    // backend/src/triviador/media/pipeline.py: oversize bytes, wrong
    // format, oversize pixels, undecodable, decompression bomb, corrupt/
    // truncated — all surfaced verbatim via
    // ApiError(MEDIA_REJECTED, 415, exc.reason) in
    // api/http/admin/media.py. A fixed sentence here could only be right
    // for one of the six.
    const oversize = adminErrorMessage(
      "media_rejected",
      "image is 999999 bytes; the limit is 1000",
    );
    const wrongFormat = adminErrorMessage(
      "media_rejected",
      "SVG is not an accepted image format; use one of BMP, GIF, JPEG, PNG, WEBP",
    );
    expect(oversize).toBe("image is 999999 bytes; the limit is 1000");
    expect(wrongFormat).toBe(
      "SVG is not an accepted image format; use one of BMP, GIF, JPEG, PNG, WEBP",
    );
    expect(oversize).not.toBe(wrongFormat);
  });

  it("falls back for import_not_confirmable, because it has 7 distinct raise sites with 7 distinct messages", () => {
    // backend/src/triviador/api/http/admin/imports.py — status/rejected
    // rows, expired, staged upload retired, staged upload no longer
    // available, staged upload changed since validated, media limits
    // changed since validated, already confirmed.
    const expired = adminErrorMessage(
      "import_not_confirmable",
      "this import expired; upload it again",
    );
    const alreadyConfirmed = adminErrorMessage(
      "import_not_confirmable",
      "this import was already confirmed",
    );
    expect(expired).toBe("this import expired; upload it again");
    expect(alreadyConfirmed).toBe("this import was already confirmed");
    expect(expired).not.toBe(alreadyConfirmed);
  });

  it("never renders an empty string, even if the server sent an unmapped code with an empty message", () => {
    expect(adminErrorMessage("validation_failed", "")).toBe("Something went wrong. Try again.");
    expect(adminErrorMessage("validation_failed", "   ")).toBe("Something went wrong. Try again.");
  });
});
