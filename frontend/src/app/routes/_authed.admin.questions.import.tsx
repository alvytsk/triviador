import { createFileRoute } from "@tanstack/react-router";
import { z } from "zod";

/**
 * Route config only, same discipline as every other admin route file in
 * this plan (see `_authed.admin.questions.$questionId.tsx`'s comment for
 * the mechanics): no `loader`, no import of `entities/admin` or
 * `generated/admin` anywhere in this file, so it stays safe to leave in
 * the eager entry graph every player's browser downloads regardless of
 * role. `ImportPage` (the lazy component, `.lazy.tsx`) owns every fetch —
 * the dry-run, the confirm, the rejected-CSV download — instead.
 *
 * `/admin/questions/import` is a leaf, not a layout: there is nothing
 * nested deeper than it (unlike `...questions.tsx`, which Task 4 had to
 * rename to `...questions.index.tsx` because a bare segment file is an
 * implicit layout for its own children), so this filename needs no
 * `.index` suffix.
 *
 * `importId` is §10.3's durable handle: the only state this screen can
 * recover after a reload, since there is no `GET` for an import's report
 * (`imports.py` only ever exposes `POST .../dry-run`, `GET
 * .../rejected.csv`, and `POST .../confirm`). A plain `z.object` of
 * primitives, not a re-export of anything `generated/admin.ts`
 * constructs — no admin-only schema identifier, so this stays inert for
 * `assert-admin-split.mjs`'s check 1 exactly like
 * `questionEditorSearchSchema` does.
 */
const importSearchSchema = z.object({
  importId: z.string().optional(),
});

export const Route = createFileRoute("/_authed/admin/questions/import")({
  validateSearch: importSearchSchema,
});
