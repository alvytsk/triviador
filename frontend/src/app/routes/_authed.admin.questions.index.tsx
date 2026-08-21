import { createFileRoute } from "@tanstack/react-router";
import { z } from "zod";
import type { Difficulty, QuestionKind } from "@/shared/api/generated/admin";

/**
 * `kind`/`difficulty` reuse the *types* `generated/admin.ts` exports, not
 * its schemas — `import type` is erased entirely by the compiler, so this
 * costs nothing at runtime, unlike importing `questionKindSchema`/
 * `difficultySchema` themselves would.
 *
 * That distinction is not pedantry: `codegen.mjs`'s own comment says why
 * `admin.ts` is a separate module in the first place — "schema
 * construction is a side-effecting top-level expression" — which means
 * Rollup cannot drop the *unused* schemas in that module once *any* eager
 * code imports *any* one of them; the whole file, `questionSavedSchema`'s
 * `duplicate_of` included, comes along. `validateSearch` runs before the
 * lazy `component` chunk ever loads (`_authed.admin.tsx`'s comment: only
 * `component` is code-split), so this file is unconditionally eager — and
 * importing `questionKindSchema`/`difficultySchema` here was verified to
 * leak `duplicate_of` into the player entry chunk (`pnpm check:bundle`
 * failing check 1) before this fix.
 *
 * `AssertExhaustive` is what still stops the two value lists below from
 * drifting from the generated types without paying that cost: adding or
 * removing a member of `QuestionKind`/`Difficulty` changes the generated
 * type, and the corresponding `_*Exhaustive` assertion below fails to
 * compile — caught by `tsc --noEmit`, same as `questionKindSchema` being
 * reused directly would have been, but for free.
 */
type AssertExhaustive<Whole, Listed extends Whole> = [Whole] extends [Listed] ? true : false;

const QUESTION_KINDS = ["multiple_choice", "numeric"] as const;
const DIFFICULTIES = ["easy", "medium", "hard"] as const;

const _questionKindsExhaustive: AssertExhaustive<QuestionKind, (typeof QUESTION_KINDS)[number]> =
  true;
const _difficultiesExhaustive: AssertExhaustive<Difficulty, (typeof DIFFICULTIES)[number]> = true;
void _questionKindsExhaustive;
void _difficultiesExhaustive;

/** §10.2's list is something an admin filters, sends to a colleague, and
 *  comes back to — search params rather than component state, so the
 *  filtered view is linkable and refresh-safe. */
export const questionSearchSchema = z.object({
  q: z.string().optional(),
  kind: z.enum(QUESTION_KINDS).optional(),
  category_id: z.string().optional(),
  difficulty: z.enum(DIFFICULTIES).optional(),
  is_active: z.boolean().optional(),
  has_media: z.boolean().optional(),
  limit: z.number().int().min(1).max(200).default(50),
  offset: z.number().int().min(0).default(0),
});

export type QuestionSearch = z.infer<typeof questionSearchSchema>;

/**
 * No `loader` here, unlike `_authed.games.$gameId.tsx`'s prefetch — and
 * that omission is deliberate, not an oversight. `_authed.admin.tsx`'s own
 * comment already warns that the router plugin's `autoCodeSplitting` only
 * ever pulls a route's `component` into its lazy chunk; a `loader` (like
 * `beforeLoad`) stays in *this* eager file, which `routeTree.gen.ts`
 * statically imports for every visitor regardless of role. A `loader` that
 * called `adminQuestionsQueryOptions` here would import `entities/admin`
 * — and, through it, every schema `generated/admin.ts` constructs,
 * `duplicate_of` included — straight into the player bundle.
 *
 * Verified, not assumed: wiring that loader back in and running
 * `pnpm check:bundle` fails check 1 with `duplicate_of found in
 * assets/index-*.js, part of the entry graph` — the exact leak this
 * comment describes. `QuestionsPage` (the lazy component) owns the fetch
 * instead, through its own `useQuery(adminQuestionsQueryOptions(...))`;
 * `keepPreviousData` (Task 2) keeps that feeling instant on every filter
 * or page change even without a router-level prefetch.
 *
 * Filename is `...questions.index.tsx`, not `...questions.tsx` (Task 4):
 * TanStack Router's file-based routing treats a bare `...questions.tsx`
 * as an implicit pathless *layout* for every deeper `...questions.*`
 * segment, which is exactly wrong here — `_authed.admin.questions.$questionId.tsx`
 * (Task 4) needs `/admin/questions` and `/admin/questions/$questionId` to
 * be siblings under `_authed/admin`, not parent and child. Proven wrong
 * the expensive way first: with the bare filename, `router.state.matches`
 * correctly included both routes on a visit to `/admin/questions/q1`, but
 * only `QuestionsPage` (this route's own component) ever painted, because
 * it renders no `<Outlet/>` for a child to mount into — the editor route
 * matched and then rendered nothing. `.index` removes the implicit-layout
 * behavior entirely, so both routes hang directly off `_authed/admin`'s
 * own `<Outlet/>` (`AdminShell`), and neither needs one of its own.
 */
export const Route = createFileRoute("/_authed/admin/questions/")({
  validateSearch: questionSearchSchema,
});
