import { createFileRoute, redirect } from "@tanstack/react-router";

/**
 * §9.7 lists no bare `/admin` screen, so landing here always moves on to
 * the question bank.
 *
 * `to`, not `href`: Task 3 registered `/admin/questions` in the route
 * tree, so `redirect()`'s target is now checked against it like any other
 * typed navigation — a typo here would fail `tsc --noEmit` instead of
 * 404ing in a browser.
 */
export const Route = createFileRoute("/_authed/admin/")({
  beforeLoad: () => {
    throw redirect({ to: "/admin/questions" });
  },
});
