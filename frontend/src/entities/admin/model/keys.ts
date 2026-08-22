import type { Difficulty, QuestionKind } from "@/shared/api/generated/admin";

/**
 * A filtered, paged question list is a different resource per page and per
 * filter set — folding either out of the key would make changing a filter
 * (or turning a page) reuse whatever the previous request cached, which is
 * a stale table, not a filtered one.
 */
export interface QuestionFilters {
  kind?: QuestionKind;
  categoryId?: string;
  difficulty?: Difficulty;
  isActive?: boolean;
  hasMedia?: boolean;
  q?: string;
}

export interface Page {
  limit: number;
  offset: number;
}

/** Every admin cache key in the application, for the same reason
 *  `entities/game/model/keys.ts` centralizes the player-facing ones: a typo
 *  in a hand-written array literal produces a second, silently-empty cache
 *  entry instead of a type error.
 *
 *  `publicPresets` is deliberately *not* namespaced under `"admin"` — `GET
 *  /api/presets` is the one route on this slice's surface that is not
 *  under `/api/admin` (Plan 7A Decision 1: any signed-in user, not just an
 *  admin, can read it), so a non-admin screen that queries the same
 *  endpoint shares this cache entry rather than colliding with it. */
export const adminKeys = {
  /** A true prefix of every `questions(filters, page)` key below — pass this
   *  to `invalidateQueries` and TanStack Query's default `exact: false`
   *  matching catches every filter/page combination in one call, rather
   *  than needing to know (or recompute) which combination is currently
   *  mounted. Added for the cache-invalidation fix: no question mutation
   *  (create, update, activate/deactivate, import-confirm) was
   *  invalidating the list at all before this, so a screen the admin had
   *  already visited kept showing the pre-mutation rows for the rest of
   *  the session (`staleTime: Infinity`, `app/query-client.ts`). */
  questionsRoot: () => ["admin", "questions"] as const,
  questions: (filters: QuestionFilters, page: Page) =>
    ["admin", "questions", filters, page] as const,
  question: (id: string) => ["admin", "question", id] as const,
  categories: () => ["admin", "categories"] as const,
  invites: () => ["admin", "invites"] as const,
  users: () => ["admin", "users"] as const,
  presets: () => ["admin", "presets"] as const,
  preset: (id: string) => ["admin", "preset", id] as const,
  coverage: (id: string) => ["admin", "preset-coverage", id] as const,
  publicPresets: () => ["presets"] as const,
} as const;
