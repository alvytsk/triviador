import type { UseQueryResult } from "@tanstack/react-query";
import { useGameSubscription } from "@/entities/game";
import { ApiFetchError, type GameSnapshot, type QuestionResolvedEvent } from "@/shared/api";
import { useMediaPrefetch } from "@/shared/lib";
import { Banner } from "@/shared/ui";
import { BoardView } from "./board-view";
import { RoomView } from "./room-view";

// A stable empty array: `?? []` would allocate a new one every render and
// re-fire `useMediaPrefetch`'s effect on every frame of the timer.
const NO_MEDIA: readonly string[] = [];

/** §9.5: "The question card skeleton reserves the stage height in advance,
 *  so nothing shifts at the moment the timer starts." The skeleton is the
 *  same three boxes at the same three heights as the real screen. */
function GameSkeleton() {
  return (
    <div className="flex h-screen flex-col overflow-hidden bg-base" aria-busy="true">
      <div className="h-24 shrink-0 bg-panel" />
      <div className="h-100 shrink-0 bg-stage" />
      <div className="grow bg-base" />
    </div>
  );
}

function GameError({ error }: { error: unknown }) {
  const failure = error instanceof ApiFetchError ? error : null;
  return (
    <div className="flex h-screen items-center justify-center p-8">
      <Banner tone="bad" {...(failure?.code != null ? { code: failure.code } : {})}>
        {failure?.message ?? "This game could not be opened."}
      </Banner>
    </div>
  );
}

/**
 * `gameQueryOptions` — §9.3's REST first paint, merged through `writeGame` —
 * lives in `app/game-query.ts` because its queryFn calls `writeGame`, which
 * is app-only (Task 7's `noRestrictedImports` gate, and steiger's
 * `fsd/forbidden-imports`: `pages` may not import from `app`, the identical
 * wall `useGameSubscription` hit and was moved to `entities/game` for). So
 * this page cannot call `useQuery(gameQueryOptions(...))` itself; the route
 * that mounts it (`app/routes/_authed.games.$gameId.tsx`) runs that query
 * and hands the live, reactive result down as `game`. Everything else this
 * page needs — the subscription and the prefetch — has no app-only
 * dependency and is called directly.
 *
 * `resolvedQuestion` is `question_resolved`'s narration event. It goes
 * through the same route-level hand-off as `game` — `useNarration` is
 * `app/socket-provider.tsx`'s, and this page cannot import it either — so
 * the route subscribes and passes the latest event down. It defaults to
 * `null` so this page still renders standalone, without a route, the way
 * this file's own tests already do.
 */
export function GamePage({
  gameId,
  game,
  resolvedQuestion = null,
}: {
  gameId: string;
  game: UseQueryResult<GameSnapshot, Error>;
  resolvedQuestion?: QuestionResolvedEvent | null;
}) {
  useGameSubscription(gameId);
  useMediaPrefetch(game.data?.state.media_prefetch ?? NO_MEDIA);

  if (game.isPending) return <GameSkeleton />;
  if (game.isError) return <GameError error={game.error} />;

  const state = game.data.state;
  return state.phase === "lobby" ? (
    <RoomView state={state} />
  ) : (
    <BoardView state={state} resolved={resolvedQuestion} />
  );
}
