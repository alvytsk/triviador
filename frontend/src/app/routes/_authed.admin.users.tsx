import { createFileRoute } from "@tanstack/react-router";

/**
 * Route config only, same discipline as every other admin route file in
 * this plan (`_authed.admin.questions.index.tsx`'s comment has the
 * mechanics, `_authed.admin.invites.tsx`'s is the most recent restatement):
 * no `loader`, no import of `entities/admin` or `generated/admin` anywhere
 * in this file, so it stays safe in the eager entry graph every player's
 * browser downloads regardless of role. `UsersPage` (the lazy component,
 * `.lazy.tsx`) owns the fetch, the role mutation and the deactivate
 * mutation instead.
 *
 * No `validateSearch`: like the invite list (its own comment says the
 * same thing), the user list has no filter and no durable in-flight id to
 * carry in the URL — `useQuery(adminUsersQueryOptions())` is the whole
 * story.
 *
 * `/admin/users` is a leaf, not a layout — no `.index` suffix needed.
 */
export const Route = createFileRoute("/_authed/admin/users")({});
