import { createFileRoute } from "@tanstack/react-router";

/**
 * Route config only, same discipline as every other admin route file in
 * this plan (`_authed.admin.questions.index.tsx`'s comment has the
 * mechanics): no `loader`, no import of `entities/admin` or
 * `generated/admin` anywhere in this file, so it stays safe in the eager
 * entry graph every player's browser downloads regardless of role.
 * `InvitesPage` (the lazy component, `.lazy.tsx`) owns the fetch, the
 * issue mutation and the revoke mutation instead.
 *
 * No `validateSearch`: unlike the question list (server-paged/filtered,
 * §10.2) or the import wizard (`importId` survives a reload, §10.3), the
 * invite list has neither a filter nor a durable in-flight id to carry —
 * `useQuery(adminInvitesQueryOptions())` is the whole story, so there is
 * nothing here worth putting in the URL.
 *
 * `/admin/invites` is a leaf, not a layout — no `.index` suffix needed
 * (same reasoning as `_authed.admin.questions.$questionId.tsx` and
 * `_authed.admin.questions.import.tsx`, both leaves too).
 */
export const Route = createFileRoute("/_authed/admin/invites")({});
