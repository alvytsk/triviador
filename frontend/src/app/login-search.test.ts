import { describe, expect, it } from "vitest";
import { loginSearchSchema } from "./routes/login";

/**
 * `next` is the redirect target `_authed`'s guard hands to `/login`, and
 * the target Task 9's post-login `navigate` will eventually send the
 * browser to. Accepting anything but a genuine same-origin path here is an
 * open redirect — a signed-in session cookie freshly minted and handed
 * straight off this origin to whatever `next` names.
 *
 * `.startsWith("/")` is not sufficient on its own: a protocol-relative URL
 * (`//evil.example/`) and a backslash variant some browsers normalise the
 * same way (`/\evil.example/`) both start with `/` and both resolve as
 * absolute, off-origin URLs. Blocking only a second `/` or `\` right after
 * the leading slash is not sufficient either: the WHATWG URL parser strips
 * ASCII tab, LF and CR from *anywhere* in a URL before resolving it, so a
 * leading `/` followed by one of those collapses to `//` and resolves
 * off-origin the same way. The control characters below are built with
 * `String.fromCharCode` rather than pasted as literal characters — a raw
 * tab/LF/CR in a string literal is invisible in review and some tools
 * strip it on save, which would silently turn the test vacuous.
 */
const TAB = String.fromCharCode(9);
const LF = String.fromCharCode(10);
const CR = String.fromCharCode(13);
const NUL = String.fromCharCode(0);
const DEL = String.fromCharCode(127);

describe("login route's next search param", () => {
  it.each([
    ["a bare slash", "/"],
    ["a normal path", "/games/g1"],
    ["a path with a query string", "/games/g1?x=1"],
    ["a path with word characters, dashes, dots and tildes", "/a-b_c.d~e"],
    ["a path with a fragment", "/games/g1#frag"],
    ["a path with a percent-encoded space", "/pa%20th"],
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
    ["a leading slash followed by a tab", `/${TAB}/evil.example/`],
    ["a leading slash followed by a line feed", `/${LF}/evil.example/`],
    ["a leading slash followed by a carriage return", `/${CR}/evil.example/`],
    ["a leading slash followed by NUL", `/${NUL}/evil.example/`],
    ["a leading slash followed by DEL", `/${DEL}/evil.example/`],
  ])("rejects %s — the open-redirect guard", (_label, next) => {
    expect(() => loginSearchSchema.parse({ next })).toThrow();
  });
});
