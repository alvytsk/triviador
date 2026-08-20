import { questionOf } from "@/entities/question";
import { SurrenderButton } from "@/features/surrender";
import type { ClientGameState, QuestionResolvedEvent } from "@/shared/api";
import { GameStage } from "@/widgets/game-stage";
import { PlayerStrip } from "@/widgets/player-strip";
import { QuestionDock } from "@/widgets/question-dock";
import { TurnDock } from "@/widgets/turn-dock";

/**
 * §9.5's geometry, in full: three bands, top to bottom — the fixed-height
 * player strip, the fixed-height stage, and the dock that grows to fill
 * whatever is left. None of the three resizes when either of the others'
 * content does, which is the whole point: nothing shifts at the moment the
 * timer starts.
 *
 * The dock band is `<QuestionDock>` when the open turn carries a question
 * and plain `<TurnDock>` otherwise (picking, targeting, warmup) — the two
 * are mutually exclusive by `Turn`'s own shape, so this is a straight
 * branch, not a merge of the two components' concerns.
 *
 * `resolved` is `question_resolved`'s narration event, threaded down from
 * `app/routes/_authed.games.$gameId.tsx` through `<GamePage>` — see
 * `<QuestionDock>`'s doc comment for why a widget cannot subscribe to it
 * itself. `connectedPlayerIds` (Task 14) travels the same hand-off, from
 * `app/use-presence.ts`'s `usePresence`, and is `<PlayerStrip>`'s alone.
 *
 * `<SurrenderButton>` sits outside the three bands, absolutely positioned,
 * for the same reason it is not a fourth row: §9.5's whole geometry exists
 * so nothing shifts when the dock's content changes, and a persistent
 * corner action does not participate in that layout at all. `pages` may
 * import a feature directly (`RoomView` already does, for `useStartGame`),
 * so this is a page composing a feature, the same shape one layer up.
 */
export function BoardView({
  state,
  resolved = null,
  connectedPlayerIds = null,
}: {
  state: ClientGameState;
  resolved?: QuestionResolvedEvent | null;
  connectedPlayerIds?: readonly string[] | null;
}) {
  return (
    <div className="relative flex h-screen flex-col overflow-hidden bg-base text-ink">
      <PlayerStrip state={state} connectedPlayerIds={connectedPlayerIds} />
      <GameStage state={state} />
      {questionOf(state) !== null ? (
        <QuestionDock state={state} resolved={resolved} />
      ) : (
        <TurnDock state={state} />
      )}
      {state.you.player_id !== null && (
        <div className="absolute top-3 right-3">
          <SurrenderButton gameId={state.game_id} />
        </div>
      )}
    </div>
  );
}
