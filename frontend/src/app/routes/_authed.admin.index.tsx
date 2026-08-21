import { createFileRoute, redirect } from "@tanstack/react-router";

/**
 * §9.7 lists no bare `/admin` screen, so landing here always moves on to
 * the question bank.
 *
 * `href` rather than `to`: `redirect()`'s `to` is checked against the
 * *registered* route tree, and `/admin/questions` is not in it yet —
 * Task 3 adds that route pair. `href` is a plain string, so this compiles
 * today and starts resolving for real the moment that route lands, with
 * no change needed here.
 */
export const Route = createFileRoute("/_authed/admin/")({
  beforeLoad: () => {
    throw redirect({ href: "/admin/questions" });
  },
});
