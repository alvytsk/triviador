import { useQuery, useQueryClient } from "@tanstack/react-query";
import { createFileRoute } from "@tanstack/react-router";
import { GamePage } from "@/pages/game";
import { gameQueryOptions } from "../game-query";

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
 */
export const Route = createFileRoute("/_authed/games/$gameId")({
  loader: ({ context, params }) =>
    context.queryClient.ensureQueryData(gameQueryOptions(params.gameId, context.queryClient)),
  component: function GameRoute() {
    const { gameId } = Route.useParams();
    const queryClient = useQueryClient();
    const game = useQuery(gameQueryOptions(gameId, queryClient));
    return <GamePage gameId={gameId} game={game} />;
  },
});
