import { createFileRoute } from "@tanstack/react-router";

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
 * `$questionId` needs no `parseParams`/`validateSearch` of its own: the id
 * segment is either a real question id or the literal string `"new"`
 * (Task 4's Step 5), and telling those apart is `QuestionEditorPage`'s
 * job, not this route's — a `z.enum`/`z.string()` guard here would still
 * be a schema constructed in the eager file's own module scope, and this
 * route needs none of that to stay dumb.
 */
export const Route = createFileRoute("/_authed/admin/questions/$questionId")({});
