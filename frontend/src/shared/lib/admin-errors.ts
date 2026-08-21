import type { ErrorCode } from "@/shared/api/generated/errors";

/** The codes Plan 7A added, each with the next action spelled out.
 *
 * `satisfies` rather than a plain annotation: a seventh admin code added
 * to the backend fails this file's type-check instead of rendering a
 * generic apology at the one moment an admin needs to know what to do. */
const ADMIN_MESSAGES = {
  media_rejected: "That image cannot be used — check the format and size, then try another.",
  import_not_confirmable: "This upload can no longer be applied. Run the dry-run again.",
  slug_taken: "A category with that slug already exists.",
  default_preset: "That is the default preset. Make another preset the default first.",
  last_admin: "This is the last administrator. Promote someone else first.",
  self_target: "You cannot do that to your own account. Ask another administrator.",
} satisfies Partial<Record<ErrorCode, string>>;

/** Looks up the admin-facing sentence for `code`, falling back to
 *  `fallback` for any code this map does not (yet, or ever) own — the
 *  wider `ErrorCode` union also carries codes every screen shares
 *  (`validation_failed`, `not_found`, …), which stay each caller's own
 *  business rather than this file's. */
export function adminErrorMessage(code: ErrorCode, fallback: string): string {
  return code in ADMIN_MESSAGES ? ADMIN_MESSAGES[code as keyof typeof ADMIN_MESSAGES] : fallback;
}
