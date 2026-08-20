import { questionOf } from "@/entities/question";
import type { ClientGameState } from "@/shared/api";
import { useBoardStore } from "@/shared/lib";
import { MapBoard } from "./map-board";

/**
 * §9.5's middle band: one fixed-height container (`h-100` = 400px, the same
 * box `GameSkeleton` reserves in `pages/game/ui/game-page.tsx`) whose
 * *content* — not its geometry — depends on whether the open turn's
 * question carries an image. A media question renders the image; every
 * other turn (including no turn at all, e.g. the warmup before the first
 * question) renders the map. One render mode, no layout shift, so nothing
 * moves at the moment the timer starts.
 *
 * Selecting a region only updates the board store's `selectedRegionId` —
 * §9.2's ephemeral UI state. Turning a selection into a `pick_region` or
 * `select_attack_target` command is Task 13's `use-command.ts` and its
 * features; this widget does not send anything.
 */
export function GameStage({ state }: { state: ClientGameState }) {
  const select = useBoardStore((s) => s.select);
  const mediaUrl = questionOf(state)?.media_url ?? null;

  return (
    <div data-testid="game-stage" className="h-100 shrink-0 overflow-hidden bg-stage">
      {mediaUrl !== null ? (
        <img src={mediaUrl} alt="Question media" className="h-full w-full object-contain" />
      ) : (
        <MapBoard state={state} onSelect={select} />
      )}
    </div>
  );
}
