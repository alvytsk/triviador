import type { ErrorCode } from "@/shared/api/generated/errors";

/** The six codes Plan 7A added, as a literal tuple rather than inferred
 *  from `ADMIN_MESSAGES`'s keys below.
 *
 * `satisfies readonly ErrorCode[]` is the other half of the exhaustiveness
 * this file promises: it guarantees every one of these six strings is a
 * real member of the generated `ErrorCode` union, so a rename upstream
 * (the code renamed or dropped entirely) breaks *this* line, rather than
 * silently leaving a stale key sitting in `ADMIN_MESSAGES` that no error
 * envelope can ever carry. */
const ADMIN_ERROR_CODES = [
  "media_rejected",
  "import_not_confirmable",
  "slug_taken",
  "default_preset",
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
  media_rejected: "That image cannot be used — check the format and size, then try another.",
  import_not_confirmable: "This upload can no longer be applied. Run the dry-run again.",
  slug_taken: "A category with that slug already exists.",
  default_preset: "That is the default preset. Make another preset the default first.",
  last_admin: "This is the last administrator. Promote someone else first.",
  self_target: "You cannot do that to your own account. Ask another administrator.",
} satisfies Record<AdminErrorCode, string>;

/** Looks up the admin-facing sentence for `code`, falling back to
 *  `fallback` for any code this map does not (yet, or ever) own — the
 *  wider `ErrorCode` union also carries codes every screen shares
 *  (`validation_failed`, `not_found`, …), which stay each caller's own
 *  business rather than this file's. */
export function adminErrorMessage(code: ErrorCode, fallback: string): string {
  return code in ADMIN_MESSAGES ? ADMIN_MESSAGES[code as AdminErrorCode] : fallback;
}
