import type { ErrorCode } from "@/shared/api/generated/errors";

/** Five of Plan 7A's six new codes, as a literal tuple rather than inferred
 *  from `ADMIN_MESSAGES`'s keys below. `default_preset` (the sixth) is
 *  deliberately NOT here — see the comment on `ADMIN_MESSAGES` for why a
 *  fixed sentence is the wrong shape for that one code.
 *
 * `satisfies readonly ErrorCode[]` is the other half of the exhaustiveness
 * this file promises: it guarantees every one of these five strings is a
 * real member of the generated `ErrorCode` union, so a rename upstream
 * (the code renamed or dropped entirely) breaks *this* line, rather than
 * silently leaving a stale key sitting in `ADMIN_MESSAGES` that no error
 * envelope can ever carry. */
const ADMIN_ERROR_CODES = [
  "media_rejected",
  "import_not_confirmable",
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
 * fail `tsc`, not just one of the two.
 *
 * `default_preset` has NO entry here, unlike the other five. Those five
 * each have exactly one trigger, so one fixed sentence is a faithful
 * summary of the server's message. `default_preset` does not: the admin
 * presets screen (Task 8) can hit it from clearing the current default
 * (`"this is the default preset; make another one default instead of
 * clearing this one"`) or from promoting a retired preset to default
 * (`"a retired preset cannot be the default; reactivate it first"`) —
 * two different refusals sharing one `code`. A fixed sentence keyed only
 * on `code` cannot distinguish them; it would show the SAME text for
 * both, which is worse than no sentence at all, because it actively
 * tells the admin the wrong reason. So `default_preset` falls through to
 * `fallback` below (the server's own `message`) instead, which is
 * already accurate per refusal — see presets.ts and `test_admin_presets.py`
 * for both exact sentences. */
const ADMIN_MESSAGES = {
  media_rejected: "That image cannot be used — check the format and size, then try another.",
  import_not_confirmable: "This upload can no longer be applied. Run the dry-run again.",
  slug_taken: "A category with that slug already exists.",
  last_admin: "This is the last administrator. Promote someone else first.",
  self_target: "You cannot do that to your own account. Ask another administrator.",
} satisfies Record<AdminErrorCode, string>;

/** Looks up the admin-facing sentence for `code`, falling back to
 *  `fallback` for any code this map does not (yet, or ever, or
 *  deliberately never — see `default_preset` above) own — the wider
 *  `ErrorCode` union also carries codes every screen shares
 *  (`validation_failed`, `not_found`, …), which stay each caller's own
 *  business rather than this file's. */
export function adminErrorMessage(code: ErrorCode, fallback: string): string {
  return code in ADMIN_MESSAGES ? ADMIN_MESSAGES[code as AdminErrorCode] : fallback;
}
