import type { ErrorCode } from "@/shared/api/generated/errors";

/** This is NOT a lookup table from code to meaning — every one of this
 *  file's 8 call sites already passes the server's own sentence as
 *  `fallback` (`grep -rn "adminErrorMessage" frontend/src`), and that
 *  sentence is always available. What lives below is an OVERRIDE list: a
 *  fixed sentence that *replaces* the server's own text with a friendlier
 *  one, for the few codes where that is safe to do.
 *
 * An override is only defensible when BOTH hold:
 *   (a) the server's message is worse for an admin than the replacement, and
 *   (b) the code is raised from exactly one backend site, so one fixed
 *       sentence cannot silently paper over two different refusals.
 *
 * (b) is not a one-time check — it was violated twice on this branch
 * before being caught (`media_rejected`: 6 raise sites in
 * `triviador/media/pipeline.py`, each with its own message, surfaced
 * verbatim via `ApiError(MEDIA_REJECTED, 415, exc.reason)` in
 * `api/http/admin/media.py`; `import_not_confirmable`: 7 raise sites in
 * `api/http/admin/imports.py`, each with its own message) and once
 * before that (`default_preset`: see below). Every code kept in this map
 * was re-verified by grepping every `raise ApiError(ApiErrorCode.<CODE>`
 * (and, for `LAST_ADMIN`, its `SetRoleOutcome` enum route) in
 * `backend/src/triviador/` at the time this comment was written:
 * `slug_taken` — one site, `api/http/admin/categories.py`. `last_admin` —
 * one site, `api/http/admin/users.py`'s `/role` route. `self_target` —
 * one site, `api/http/admin/users.py`'s `/deactivate` route. That is a
 * fact about the backend today, not a promise about tomorrow — if any of
 * these three grows a second raise site, its entry here goes stale the
 * same way `media_rejected`'s and `import_not_confirmable`'s did, and
 * this file will not know. Whoever adds that second site is on the hook
 * to remove the entry.
 *
 * `default_preset` has no entry: it is raised from THREE sites (two in
 * `PATCH /{preset_id}` — clearing the current default, and promoting a
 * retired preset to default — and one in `DELETE /{preset_id}` —
 * retiring the current default), each with its own message, so a fixed
 * sentence keyed only on `code` cannot distinguish them without lying
 * about which refusal happened. `media_rejected` and
 * `import_not_confirmable` fail rule (b) the same way `default_preset`
 * does, so they are not overridden either — all three fall through to
 * `fallback` (the server's own message), which is already accurate per
 * call site. */
const ADMIN_ERROR_CODES = [
  "slug_taken",
  "last_admin",
  "self_target",
] as const satisfies readonly ErrorCode[];

type AdminErrorCode = (typeof ADMIN_ERROR_CODES)[number];

/** Each with the next action spelled out.
 *
 * `satisfies Record<AdminErrorCode, string>` — not `Partial` — is the
 * first half of exhaustiveness: `Record` (unlike `Partial<Record<...>>`)
 * requires every key of `AdminErrorCode` to be present, so deleting an
 * entry here fails the build instead of quietly rendering a generic
 * apology at the one moment an admin needs to know what to do. Together
 * with `ADMIN_ERROR_CODES` above, both a dropped code and a mistyped key
 * fail `tsc`, not just one of the two. */
const ADMIN_MESSAGES = {
  slug_taken: "A category with that slug already exists.",
  last_admin: "This is the last administrator. Promote someone else first.",
  self_target: "You cannot do that to your own account. Ask another administrator.",
} satisfies Record<AdminErrorCode, string>;

/** Looks up the admin-facing override sentence for `code`, falling back
 *  to `fallback` (the caller's own — always the server's `message`) for
 *  any code this map does not own — either because it never will (see
 *  the file comment above) or because it is a code every screen shares
 *  (`validation_failed`, `not_found`, …), which stays each caller's own
 *  business rather than this file's.
 *
 * `fallback` is guarded rather than rendered blindly: today every caller
 * passes a non-empty server `message`, but nothing enforces that at the
 * type level, and a `Banner` rendering literally nothing is a worse
 * failure than a generic line. */
export function adminErrorMessage(code: ErrorCode, fallback: string): string {
  if (code in ADMIN_MESSAGES) {
    return ADMIN_MESSAGES[code as AdminErrorCode];
  }
  return fallback.trim().length > 0 ? fallback : "Something went wrong. Try again.";
}
