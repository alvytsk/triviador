import { useQuery } from "@tanstack/react-query";
import { useEffect } from "react";
import { lobbyQueryOptions } from "@/entities/game";
import { CreateGamePanel } from "@/features/create-game";
import { useSocket } from "@/shared/api";
import { GameRow } from "./game-row";

/**
 * §9.3's pattern in its simplest form: `GET /api/games` (via
 * `lobbyQueryOptions`) fills the list on first paint, and the dispatcher
 * already writes every `lobby.snapshot` / `lobby.update` into `["lobby"]`
 * for as long as this screen holds the `lobby` topic — so this component
 * only ever reads the query, never the socket message itself.
 *
 * The subscribe/unsubscribe is a plain effect, not `useGameSubscription`'s
 * refcounted one: that hook exists because two widgets can want the same
 * *game* topic at once, and exactly one screen — this one — ever holds
 * `lobby`.
 */
export function LobbyPage() {
  const { send } = useSocket();
  const lobby = useQuery(lobbyQueryOptions());

  useEffect(() => {
    send({ type: "subscribe", topic: "lobby" });
    return () => send({ type: "unsubscribe", topic: "lobby" });
  }, [send]);

  const games = lobby.data ?? [];

  return (
    <div className="flex min-h-screen justify-center gap-8 bg-base px-6 py-10 text-ink">
      <div className="flex w-full max-w-3xl flex-col gap-4">
        <h1 className="font-display text-4xl tracking-wider text-gold">LOBBY</h1>

        {games.length === 0 ? (
          <p className="border border-line bg-panel px-5 py-10 text-center text-[13px] text-ink-dim">
            No open games. Start one on the right.
          </p>
        ) : (
          <ul className="flex flex-col gap-3">
            {games.map((game) => (
              <GameRow key={game.game_id} game={game} />
            ))}
          </ul>
        )}
      </div>

      <CreateGamePanel />
    </div>
  );
}
