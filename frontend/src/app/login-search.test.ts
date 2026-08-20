import { describe, expect, it } from "vitest";
import { loginSearchSchema } from "./routes/login";

/**
 * `next` is the redirect target `_authed`'s guard hands to `/login`, and
 * the target Task 9's post-login `navigate` will eventually send the
 * browser to. Accepting anything but a genuine same-origin path here is an
 * open redirect — a signed-in session cookie freshly minted and handed
 * straight off this origin to whatever `next` names.
 *
 * `.startsWith("/")` is not sufficient: a protocol-relative URL
 * (`//evil.example/`) and a backslash variant some browsers normalise the
 * same way (`/\evil.example/`) both start with `/` and both resolve as
 * absolute, off-origin URLs. The schema's regex — a single leading `/` not
 * followed by another `/` or a `\` — is what actually closes this.
 */
describe("login route's next search param", () => {
  it.each([
    ["a bare slash", "/"],
    ["a normal path", "/games/g1"],
    ["a path with a query string", "/games/g1?x=1"],
  ])("accepts %s", (_label, next) => {
    expect(loginSearchSchema.parse({ next })).toEqual({ next });
  });

  it("accepts no next at all", () => {
    expect(loginSearchSchema.parse({})).toEqual({});
  });

  it.each([
    ["a protocol-relative URL", "//evil.example/"],
    ["a backslash variant", "/\\evil.example/"],
    ["an absolute https URL", "https://evil.example/"],
    ["a javascript: URL", "javascript:alert(1)"],
    ["the empty string", ""],
  ])("rejects %s — the open-redirect guard", (_label, next) => {
    expect(() => loginSearchSchema.parse({ next })).toThrow();
  });
});
