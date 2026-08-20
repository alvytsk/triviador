import { orderedPlayers } from "@/entities/player";
import { useStartGame } from "@/features/start-game";
import { ApiFetchError, type ClientGameState } from "@/shared/api";
import { seatVar } from "@/shared/config";
import { Banner, Button, Chip } from "@/shared/ui";

/**
 * `phase === "lobby"`: every seat up to `rules.player_count`, filled or
 * empty, the rules readout, and one Start button.
 *
 * Plan 5's `start_game` authorizes any seated participant, not only a host,
 * so Start is offered to any player who has a seat — `state.you.player_id`
 * being non-null already tells us that (§8.7: the projection carries `you`
 * for exactly this). The button does not go on to compute the
 * not-enough-players rule itself: it attempts the command and shows
 * whatever the server refuses it with, the same pattern
 * `CreateGamePanel`/`GameRow` already use for `no_default_preset` and a full
 * game — the reason is the server's, never reimplemented here.
 */
export function RoomView({ state }: { state: ClientGameState }) {
  const startGame = useStartGame();
  const bySeat = new Map(orderedPlayers(state).map((player) => [player.seat, player]));
  const seats = Array.from(
    { length: state.rules.player_count },
    (_, seat) => bySeat.get(seat) ?? null,
  );
  const youAreSeated = state.you.player_id !== null;

  return (
    <div className="flex min-h-screen flex-col items-center gap-8 bg-base px-6 py-10 text-ink">
      <h1 className="font-display text-4xl tracking-wider text-gold">GAME ROOM</h1>

      <ul className="flex w-full max-w-md flex-col gap-2">
        {seats.map((player, seat) => (
          <li
            // biome-ignore lint/suspicious/noArrayIndexKey: the seat number *is* the index — a fixed-length roster, not a reorderable list.
            key={seat}
            className="flex items-center gap-3 border border-line bg-panel px-4 py-3"
            style={{ borderLeftColor: seatVar(seat), borderLeftWidth: 4 }}
          >
            {player !== null ? (
              <>
                <span className="text-[13px] font-semibold">{player.display_name}</span>
                {player.player_id === state.you.player_id && <Chip>YOU</Chip>}
              </>
            ) : (
              <span className="text-[13px] text-ink-faint">Empty seat</span>
            )}
          </li>
        ))}
      </ul>

      <p className="w-full max-w-md border-t border-line pt-4 text-[13px] text-ink-dim">
        {state.rules.player_count} players · {state.rules.expansion_rounds} expansion rounds ·{" "}
        {state.rules.battle_rounds} battle rounds
      </p>

      {startGame.error instanceof ApiFetchError && (
        <Banner
          tone="bad"
          {...(startGame.error.code !== null ? { code: startGame.error.code } : {})}
        >
          {startGame.error.message}
        </Banner>
      )}

      <Button
        onClick={() => startGame.mutate(state.game_id)}
        disabled={!youAreSeated || startGame.isPending}
      >
        {startGame.isPending ? "Starting…" : "Start game"}
      </Button>
    </div>
  );
}
