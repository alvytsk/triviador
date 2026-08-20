import { createFileRoute } from "@tanstack/react-router";
import { z } from "zod";

/**
 * Placeholder. `_authed`'s guard (`beforeLoad`) already redirects here with
 * `search: { next }` on an unauthenticated session, and TanStack Router's
 * typed `redirect` needs `/login` to exist as a real route — with this
 * exact search shape — for that call to type-check. Task 9 replaces the
 * component with the real login form; the search schema is expected to
 * survive unchanged, since the guard already depends on it.
 *
 * `next` is constrained to a same-origin path (`.startsWith("/")`) rather
 * than any string. Without that, `next` is an open redirect: an attacker
 * links a victim to `/login?next=https://evil.example/`, the victim signs
 * in for real, and Task 9's post-login `navigate({ to: search.next })`
 * would ship them straight off this origin with a fresh session cookie in
 * hand. Dead code today — this route renders nothing — but the schema is
 * what Task 9 inherits, so the guard belongs here, not there.
 */
export const loginSearchSchema = z.object({ next: z.string().startsWith("/").optional() });

export const Route = createFileRoute("/login")({
  validateSearch: loginSearchSchema,
  component: () => null,
});
