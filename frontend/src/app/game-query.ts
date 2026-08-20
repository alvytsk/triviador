import { type QueryClient, queryOptions } from "@tanstack/react-query";
import { gameKey } from "@/entities/game";
import { apiFetch, type GameSnapshot, gameSnapshotSchema } from "@/shared/api";
import { writeGame } from "./dispatcher";

/**
 * §9.3: "`["game", id]` has a real `queryFn` — `GET /games/{id}` — returning
 * the same `GameSnapshot` through the same `project_snapshot`. One
 * projection, two transports: the page survives a refresh and renders while
 * the socket is still connecting."
 *
 * The REST response can land *after* a newer socket update. Rather than
 * letting TanStack write whatever it fetched, the fetch is merged through
 * `writeGame` and the query returns what the cache now holds — so there is
 * still exactly one rule deciding which of two versions is newer, and
 * TanStack's own write is a no-op re-set of the same object reference.
 *
 * This lives here, not in `entities/game/api/`, because the queryFn calls
 * `writeGame` — app-only, per Task 7's `noRestrictedImports` gate on
 * `@/app/dispatcher` and steiger's `fsd/forbidden-imports` alike. That also
 * means nothing below `app/` (in particular `pages/game`) may import this
 * function directly — see `app/routes/_authed.games.$gameId.tsx`, the one
 * place both this and `GamePage` may be imported together.
 */
export function gameQueryOptions(gameId: string, queryClient: QueryClient) {
  return queryOptions({
    queryKey: gameKey(gameId),
    queryFn: async (): Promise<GameSnapshot> => {
      const fetched = await apiFetch(`/api/games/${gameId}`, gameSnapshotSchema);
      writeGame(queryClient, gameId, fetched);
      return queryClient.getQueryData<GameSnapshot>(gameKey(gameId)) ?? fetched;
    },
  });
}
