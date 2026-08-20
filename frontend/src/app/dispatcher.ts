import type { QueryClient } from "@tanstack/react-query";
import { gameKey, lobbyKey } from "@/entities/game";
import { type GameSnapshot, parseClientEvent, type ServerMessage } from "@/shared/api";
import type { EventBus } from "./event-bus";
import type { PresenceStore } from "./use-presence";

/**
 * §9.3's merge rule, and the only one in the application.
 *
 * Both paths into `["game", id]` go through it — the dispatcher today, and
 * Task 12's REST first paint once it exists — so there is exactly one place
 * where two versions of a game are compared and exactly one answer to
 * "which is newer". `>=` rather than `>` on purpose: a resync after a
 * reconnect can legitimately carry the seq the cache already holds, and it
 * must still land, because the *state* may have been rebuilt while the seq
 * stood still.
 */
export function writeGame(queryClient: QueryClient, gameId: string, incoming: GameSnapshot): void {
  queryClient.setQueryData<GameSnapshot>(gameKey(gameId), (previous) =>
    previous === undefined || incoming.seq >= previous.seq ? incoming : previous,
  );
}

/**
 * The ws→cache dispatcher §9.4 puts in `app/` — the one module allowed to
 * know both that a socket exists and that a cache does.
 *
 * Spec 1B §8.2's gap rule, verbatim:
 *
 *     base_seq == last_seq            apply state, emit narration events
 *     seq <= last_seq                 duplicate — ignore
 *     seq > last_seq, base mismatch   apply full state, suppress events
 *
 * `last_seq` is not a variable here: it is `cache[gameKey(id)].seq`. Keeping
 * it in a `Map` beside the cache would create two facts that can disagree —
 * and they would, the first time a REST first paint landed between two
 * updates. Deriving it means the REST race and the gap rule are settled by
 * the same number.
 */
export function createDispatcher(deps: {
  queryClient: QueryClient;
  bus: EventBus;
  presence: PresenceStore;
}) {
  const { queryClient, bus, presence } = deps;

  function lastSeq(gameId: string): number | null {
    return queryClient.getQueryData<GameSnapshot>(gameKey(gameId))?.seq ?? null;
  }

  return {
    handle(message: ServerMessage): void {
      switch (message.type) {
        case "game.snapshot":
          // A snapshot is the truth as of `seq` and narrates nothing: §8.5's
          // recovery is "take a fresh state", not "replay what you missed".
          writeGame(queryClient, message.game_id, { seq: message.seq, state: message.state });
          return;

        case "game.update": {
          const last = lastSeq(message.game_id);
          if (last !== null && message.seq <= last) return; // duplicate

          const contiguous = last !== null && message.base_seq === last;
          writeGame(queryClient, message.game_id, { seq: message.seq, state: message.state });
          if (!contiguous) {
            // §8.2: because every update carries full state, a gap costs an
            // animation, not correctness — and does not require a resync.
            return;
          }
          const narration = message.events
            .map((event: unknown) => parseClientEvent(event))
            .filter((event): event is NonNullable<typeof event> => event !== null);
          bus.emit(message.game_id, narration);
          return;
        }

        case "lobby.snapshot":
        case "lobby.update":
          queryClient.setQueryData(lobbyKey(), message.games);
          return;

        case "game.presence":
          // Task 14: fed into `PresenceStore`, never the query cache — see
          // `app/use-presence.ts`'s doc comment for why `game.presence` gets
          // a store of its own rather than a place beside `GameSnapshot`.
          presence.update(message.game_id, message.connected);
          return;

        case "hello":
        case "pong":
        case "error":
          // Errors are correlated by `command_id` at the call site (Task
          // 13); `hello`/`pong` carry nothing worth keeping. Neither is
          // state, and neither belongs in a cache.
          return;
      }
    },
  };
}
