import { createFileRoute } from "@tanstack/react-router";
import { z } from "zod";
import { LoginPage } from "@/pages/login";

/**
 * Placeholder. `_authed`'s guard (`beforeLoad`) already redirects here with
 * `search: { next }` on an unauthenticated session, and TanStack Router's
 * typed `redirect` needs `/login` to exist as a real route — with this
 * exact search shape — for that call to type-check. Task 9 replaces the
 * component with the real login form; the search schema is expected to
 * survive unchanged, since the guard already depends on it.
 *
 * `next` is constrained to a single leading `/` — not followed by another
 * `/` or a `\`, and containing no C0 control character or DEL anywhere in
 * the rest of the string — rather than any string. Without that, `next` is
 * an open redirect: an attacker links a victim to
 * `/login?next=https://evil.example/`, the victim signs in for real, and
 * Task 9's post-login `navigate({ to: search.next })` would ship them
 * straight off this origin with a fresh session cookie in hand.
 *
 * Do not "simplify" this back to `.startsWith("/")` or to
 * `/^\/(?![/\\])/` without the trailing character class — both look
 * sufficient and neither is:
 *   - `.startsWith("/")` alone accepts `//evil.example/` (protocol-relative,
 *     resolves as an absolute same-scheme URL) and `/\evil.example/` (a
 *     backslash variant some browsers normalise the same way).
 *   - Blocking a second `/` or `\` right after the first character is not
 *     enough either: the WHATWG URL parser strips ASCII tab, LF and CR from
 *     *anywhere* in a URL before resolving it, so `/<TAB>/evil.example/`
 *     collapses to `//evil.example/` and resolves off-origin the same way.
 *     `[^\x00-\x1F\x7F]*` after the anchor is what rules out every C0
 *     control and DEL, not just the two right after the leading slash. A
 *     legitimate path never contains one of these characters, which is
 *     exactly why anything that does is an attack, not a false positive.
 *
 * Dead code today — this route renders nothing — but the schema is what
 * Task 9 inherits, so the guard belongs here, not there.
 */
export const loginSearchSchema = z.object({
  next: z
    .string()
    // biome-ignore lint/suspicious/noControlCharactersInRegex: deliberate — this range is the open-redirect guard documented above, not a mistake.
    .regex(/^\/(?![/\\])[^\x00-\x1F\x7F]*$/)
    .optional(),
});

export const Route = createFileRoute("/login")({
  validateSearch: loginSearchSchema,
  component: LoginPage,
});
