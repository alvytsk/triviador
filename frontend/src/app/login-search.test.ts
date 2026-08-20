import { describe, expect, it } from "vitest";
import { loginSearchSchema } from "./routes/login";

/**
 * `next` is the redirect target `_authed`'s guard hands to `/login`, and
 * the target Task 9's post-login `navigate` will eventually send the
 * browser to. Accepting an absolute URL here is an open redirect — a
 * signed-in session cookie freshly minted and handed straight off this
 * origin to whatever `next` names. `.startsWith("/")` is the one thing
 * standing between "redirect back to where you were" and that.
 */
describe("login route's next search param", () => {
  it("accepts a same-origin relative path", () => {
    expect(loginSearchSchema.parse({ next: "/games/g1" })).toEqual({ next: "/games/g1" });
  });

  it("accepts no next at all", () => {
    expect(loginSearchSchema.parse({})).toEqual({});
  });

  it("rejects an absolute URL — the open-redirect guard", () => {
    expect(() => loginSearchSchema.parse({ next: "https://evil.example/" })).toThrow();
  });
});
