import { createFileRoute } from "@tanstack/react-router";
import { z } from "zod";

/**
 * Placeholder. `_authed`'s guard (`beforeLoad`) already redirects here with
 * `search: { next }` on an unauthenticated session, and TanStack Router's
 * typed `redirect` needs `/login` to exist as a real route — with this
 * exact search shape — for that call to type-check. Task 9 replaces the
 * component with the real login form; the search schema is expected to
 * survive unchanged, since the guard already depends on it.
 */
const loginSearchSchema = z.object({ next: z.string().optional() });

export const Route = createFileRoute("/login")({
  validateSearch: loginSearchSchema,
  component: () => null,
});
