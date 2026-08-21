import type { Difficulty, QuestionKind } from "@/shared/api/generated/admin";

/**
 * Mirrors `_authed.admin.questions.tsx`'s `questionSearchSchema` shape —
 * not imported from there, because that file lives in `app/`, and `pages`
 * may not import `app` (FSD's own direction, the wrong way). Every value
 * this page actually reads comes from `useSearch({ from: "/_authed/admin/questions" })`,
 * which TanStack Router types correctly against the *registered* route
 * without needing that import either — this interface exists only so
 * `QuestionFilterBar`, `QuestionTable` and `QuestionPager` can name the
 * shape without repeating it three times.
 *
 * `QuestionKind`/`Difficulty` are `import type` — erased entirely, so
 * unlike importing `questionKindSchema`/`difficultySchema` themselves,
 * this costs nothing in the bundle and cannot be the thing that pulls
 * `entities/admin` into a chunk it shouldn't be in.
 *
 * Every optional field is `T | undefined`, not just `?: T` — matching how
 * Zod's `.optional()` actually infers under this project's
 * `exactOptionalPropertyTypes`, which is what `useSearch`/`useNavigate`
 * are typed against for the real route. Writing it any narrower would
 * make this interface fail to describe the values those hooks actually
 * hand back.
 */
export interface QuestionSearch {
  q?: string | undefined;
  kind?: QuestionKind | undefined;
  category_id?: string | undefined;
  difficulty?: Difficulty | undefined;
  is_active?: boolean | undefined;
  has_media?: boolean | undefined;
  limit: number;
  offset: number;
}

/** The wire shape (snake_case, one flat object) versus `entities/admin`'s
 *  `AdminQuestionSearch` (`filters`/`page`, camelCase `categoryId`/
 *  `isActive`/`hasMedia`) — translated once, here, rather than asking
 *  every caller of `adminQuestionsQueryOptions` to know both shapes.
 *
 *  Keys are included only when defined, rather than always present and
 *  sometimes `undefined` — `entities/admin`'s `QuestionFilters` (Task 2,
 *  off-limits here) declares each field as plain `?: T`, and under
 *  `exactOptionalPropertyTypes` that rejects an explicit `key: undefined`
 *  even though the key is optional. */
export function toAdminQuestionSearch(search: QuestionSearch) {
  return {
    filters: {
      ...(search.kind !== undefined && { kind: search.kind }),
      ...(search.category_id !== undefined && { categoryId: search.category_id }),
      ...(search.difficulty !== undefined && { difficulty: search.difficulty }),
      ...(search.is_active !== undefined && { isActive: search.is_active }),
      ...(search.has_media !== undefined && { hasMedia: search.has_media }),
      ...(search.q !== undefined && { q: search.q }),
    },
    page: { limit: search.limit, offset: search.offset },
  };
}

/** Whether any filter (as opposed to just pagination) is set — the one
 *  fact that tells an empty question bank apart from an empty filter. */
export function hasActiveFilters(search: QuestionSearch): boolean {
  return (
    search.q !== undefined ||
    search.kind !== undefined ||
    search.category_id !== undefined ||
    search.difficulty !== undefined ||
    search.is_active !== undefined ||
    search.has_media !== undefined
  );
}
