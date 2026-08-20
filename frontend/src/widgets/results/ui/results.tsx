import { Link } from "@tanstack/react-router";
import type { ClientGameState, GameAbortedEvent } from "@/shared/api";
import { seatVar } from "@/shared/config";
import { Chip } from "@/shared/ui";

/**
 * Full time. Renders for `phase === "finished"` and `phase === "aborted"` —
 * the two terminal phases `GamePage` hands off to this widget instead of
 * `<BoardView>`.
 *
 * Finished: the winner named from `winner_id` (never re-derived from the
 * roster — the server decided it), then every player ranked by `score`
 * alone. `bonus_score` is shown beside it, never folded in: §9.1's whole
 * point is that the client never adds anything up, and a ranking that
 * silently summed `score + bonus_score` would be exactly that.
 *
 * Aborted: `aborted` is `game_aborted`'s narration event, threaded down from
 * `app/routes/_authed.games.$gameId.tsx` the same way Task 13 threaded
 * `question_resolved` into `<QuestionDock>` — `useNarration` is
 * `app/socket-provider.tsx`'s and this widget may not call it itself
 * (`fsd/forbidden-imports`). It defaults to `null` so a screen opened after
 * the event has already come and gone — a refresh, a late navigation — still
 * says something rather than nothing: "This game was ended."
 */
export function Results({
  state,
  aborted = null,
}: {
  state: ClientGameState;
  aborted?: GameAbortedEvent | null;
}) {
  if (state.phase === "aborted") {
    return (
      <div className="flex min-h-screen flex-col items-center justify-center gap-6 bg-base px-6 py-10 text-ink">
        <h1 className="font-display text-4xl tracking-wider text-gold">GAME OVER</h1>
        <p className="text-[15px] text-ink-dim">{aborted?.reason ?? "This game was ended."}</p>
        <Link to="/" className="text-[13px] font-semibold uppercase tracking-[0.14em] text-gold">
          Back to the lobby
        </Link>
      </div>
    );
  }

  const winner = state.players.find((player) => player.player_id === state.winner_id) ?? null;
  const ranked = [...state.players].sort((a, b) => b.score - a.score);

  return (
    <div className="flex min-h-screen flex-col items-center gap-6 bg-base px-6 py-10 text-ink">
      <h1 className="font-display text-4xl tracking-wider text-gold">RESULTS</h1>
      <p className="text-[15px] text-ink-dim">
        {winner !== null ? (
          <>
            Winner:{" "}
            <span data-testid="results-winner" className="font-semibold text-gold">
              {winner.display_name}
            </span>
          </>
        ) : (
          "No winner"
        )}
      </p>

      <ol className="flex w-full max-w-md flex-col gap-2">
        {ranked.map((player, index) => (
          <li
            key={player.player_id}
            data-testid={`result-${player.player_id}`}
            className="flex items-center gap-3 border border-line bg-panel px-4 py-3"
            style={{ borderLeftColor: seatVar(player.seat), borderLeftWidth: 4 }}
          >
            <span className="w-4 text-[13px] text-ink-faint">{index + 1}</span>
            <span className="flex-1 truncate text-[13px] font-semibold">{player.display_name}</span>
            {player.player_id === state.winner_id && <Chip>WINNER</Chip>}
            <span className="text-[13px] text-gold">{player.score}</span>
            <span className="text-[11px] text-ink-dim">+{player.bonus_score} bonus</span>
          </li>
        ))}
      </ol>

      <Link to="/" className="text-[13px] font-semibold uppercase tracking-[0.14em] text-gold">
        Back to the lobby
      </Link>
    </div>
  );
}
