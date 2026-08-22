import { createFileRoute } from "@tanstack/react-router";
import { z } from "zod";

/**
 * Route config only, same discipline as `_authed.admin.questions.index.tsx`
 * (Task 3): no `loader`, no import of `entities/admin` or
 * `generated/admin` anywhere in this file. `_authed.admin.tsx`'s own
 * comment already explains why — the router plugin's `autoCodeSplitting`
 * only ever pulls a route's `component` into its own lazy chunk, so this
 * file stays in the eager entry graph every player's browser downloads
 * regardless of role. `QuestionEditorPage` (the lazy component,
 * `.lazy.tsx`) owns every fetch and every mutation instead — loading the
 * question by id, the category list, the save, the media upload, and the
 * activate/deactivate toggle all happen from inside it.
 *
 * `$questionId` needs no `parseParams` of its own: the id segment is
 * either a real question id or the literal string `"new"` (Task 4's Step
 * 5), and telling those apart is `QuestionEditorPage`'s job, not this
 * route's.
 *
 * `validateSearch` *does* earn its keep here, unlike the sibling comment
 * above once suggested: `duplicateOf` is how a create's §10.2 duplicate
 * warning survives the redirect from `/admin/questions/new` to the saved
 * question's canonical id (`question-editor-page.tsx`'s `handleSaved`
 * navigates with `replace: true`, unmounting the form instance that held
 * the warning locally — a search param is what a page can still read
 * after that remount, the same durable, refresh-safe pattern
 * `questionSearchSchema` already uses for the list's filters). This is a
 * plain `z.object` of primitives, not a re-export of anything
 * `generated/admin.ts` constructs — no admin-only schema identifier, so
 * this stays inert for `assert-admin-split.mjs`'s check 1 exactly like
 * `questionSearchSchema` does.
 */
const questionEditorSearchSchema = z.object({
  duplicateOf: z.array(z.string()).optional(),
});

export const Route = createFileRoute("/_authed/admin/questions/$questionId")({
  validateSearch: questionEditorSearchSchema,
});
