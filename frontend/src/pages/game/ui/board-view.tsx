import type { ClientGameState } from "@/shared/api";
import { GameStage } from "@/widgets/game-stage";
import { PlayerStrip } from "@/widgets/player-strip";
import { TurnDock } from "@/widgets/turn-dock";

/**
 * §9.5's geometry, in full: three bands, top to bottom — the fixed-height
 * player strip, the fixed-height stage, and the dock that grows to fill
 * whatever is left. None of the three resizes when either of the others'
 * content does, which is the whole point: nothing shifts at the moment the
 * timer starts.
 */
export function BoardView({ state }: { state: ClientGameState }) {
  return (
    <div className="flex h-screen flex-col overflow-hidden bg-base text-ink">
      <PlayerStrip state={state} />
      <GameStage state={state} />
      <TurnDock state={state} />
    </div>
  );
}
