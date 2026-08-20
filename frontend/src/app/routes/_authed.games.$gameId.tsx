import { useQuery, useQueryClient } from "@tanstack/react-query";
import { createFileRoute } from "@tanstack/react-router";
import { useState } from "react";
import { GamePage } from "@/pages/game";
import type { GameAbortedEvent, QuestionResolvedEvent } from "@/shared/api";
import { gameQueryOptions } from "../game-query";
import { useNarration } from "../socket-provider";
import { usePresence } from "../use-presence";

/**
 * The loader pre-warms `["game", id]` through `gameQueryOptions`'s one merge
 * rule (§9.3) so navigation from create/join — which never write that cache
 * themselves, see `features/create-game`/`features/join-game` — lands with
 * the snapshot already there.
 *
 * The component's own `useQuery(gameQueryOptions(...))` is what actually
 * keeps `GamePage` subscribed to that cache entry afterwards, and it has to
 * run *here*: `gameQueryOptions` lives in `app/` because its queryFn calls
 * `writeGame`, which is app-only (Task 7's `noRestrictedImports` gate, and
 * steiger's `fsd/forbidden-imports`), and `pages` may not import from `app`
 * — the identical wall Task 10 hit for `useSocket`, resolved there by moving
 * the socket-consuming parts down to `shared`/`entities`. `gameQueryOptions`
 * cannot move down the same way (it needs `writeGame`), so instead this
 * route — the one place both `gameQueryOptions` and `GamePage` may be
 * imported together — runs the query and hands the live result down as a
 * prop.
 *
 * The same wall applies to `useNarration` — it lives on `SocketProvider`'s
 * richer, app-only context (for `bus`), so `<QuestionDock>` cannot call it
 * itself. This route is the one place that can: it keeps the latest
 * `question_resolved` event in state, clears it on `question_presented`
 * (the event the schema itself documents as "the cue" a fresh question
 * turn has begun — see `questionPresentedEventSchema`), and hands the
 * result down through `<GamePage>` the same way it hands down `game`. Task
 * 14 adds `game_aborted` to the same subscription, for `<Results>`.
 *
 * `usePresence` (`app/use-presence.ts`) is behind the identical wall, for
 * the identical reason — `<PlayerStrip>` cannot call it either — so this
 * route reads it too and hands the connected roster down as
 * `connectedPlayerIds`.
 */
export const Route = createFileRoute("/_authed/games/$gameId")({
  loader: ({ context, params }) =>
    context.queryClient.ensureQueryData(gameQueryOptions(params.gameId, context.queryClient)),
  component: function GameRoute() {
    const { gameId } = Route.useParams();
    const queryClient = useQueryClient();
    const game = useQuery(gameQueryOptions(gameId, queryClient));
    const [resolvedQuestion, setResolvedQuestion] = useState<QuestionResolvedEvent | null>(null);
    const [aborted, setAborted] = useState<GameAbortedEvent | null>(null);
    useNarration(gameId, (event) => {
      if (event.type === "question_resolved") setResolvedQuestion(event);
      else if (event.type === "question_presented") setResolvedQuestion(null);
      else if (event.type === "game_aborted") setAborted(event);
    });
    const connectedPlayerIds = usePresence(gameId);
    return (
      <GamePage
        gameId={gameId}
        game={game}
        resolvedQuestion={resolvedQuestion}
        aborted={aborted}
        connectedPlayerIds={connectedPlayerIds}
      />
    );
  },
});
