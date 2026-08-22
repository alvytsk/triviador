import { createFileRoute } from "@tanstack/react-router";

/**
 * Route config only, same discipline as every other admin route file in
 * this plan (`_authed.admin.users.tsx`'s comment has the most recent
 * restatement): no `loader`, no import of `entities/admin` or
 * `generated/admin` anywhere in this file, so it stays safe in the eager
 * entry graph every player's browser downloads regardless of role.
 * `PresetsPage` (the lazy component, `.lazy.tsx`) owns the fetch and both
 * mutations instead.
 *
 * No `validateSearch`: selection lives in the page's own React state
 * (`PresetsPage`'s own comment says why) rather than the URL, so there is
 * nothing here to parse.
 *
 * `/admin/presets` is a leaf, not a layout — no `.index` suffix needed.
 */
export const Route = createFileRoute("/_authed/admin/presets")({});
