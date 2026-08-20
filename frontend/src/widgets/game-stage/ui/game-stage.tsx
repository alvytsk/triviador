import { deadlineIdOf, turnKindOf } from "@/entities/game";
import { questionOf } from "@/entities/question";
import { PickRegionStatus, usePickRegion } from "@/features/pick-region";
import { SelectTargetStatus, useSelectTarget } from "@/features/select-target";
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
 * Selecting a region always updates the board store's `selectedRegionId` —
 * §9.2's ephemeral UI state, for the click's own visual feedback. Task 12
 * left it at that; Task 13 turns the same click into a `pick_region` or
 * `select_attack_target` command when the open turn is one of those two
 * kinds, via `usePickRegion`/`useSelectTarget` — mounted only for the turn
 * kind that is actually live, so at most one `useCommand()` instance from
 * this widget is ever pending at once (a question turn's own command lives
 * in `<QuestionDock>`, mounted separately, for the same reason: two
 * concurrent `useCommand()` instances would each react to *every* `error`
 * frame regardless of which one sent the command that provoked it, and the
 * only thing that keeps that harmless here is that exactly one of
 * pick/target/answer is ever the live turn).
 */
export function GameStage({ state }: { state: ClientGameState }) {
  const select = useBoardStore((s) => s.select);
  const mediaUrl = questionOf(state)?.media_url ?? null;
  const kind = turnKindOf(state);

  return (
    <div data-testid="game-stage" className="relative h-100 shrink-0 overflow-hidden bg-stage">
      {mediaUrl !== null ? (
        <img src={mediaUrl} alt="Question media" className="h-full w-full object-contain" />
      ) : kind === "expansion_picking" ? (
        <PickRegionBoard state={state} select={select} />
      ) : kind === "battle_target_select" ? (
        <SelectTargetBoard state={state} select={select} />
      ) : (
        <MapBoard state={state} onSelect={select} />
      )}
    </div>
  );
}

function PickRegionBoard({
  state,
  select,
}: {
  state: ClientGameState;
  select: (regionId: string | null) => void;
}) {
  const { pick, failure } = usePickRegion(state.game_id, deadlineIdOf(state));
  return (
    <>
      <MapBoard
        state={state}
        onSelect={(regionId) => {
          select(regionId);
          pick(regionId);
        }}
      />
      <div className="pointer-events-none absolute inset-x-0 bottom-0 p-3">
        <div className="pointer-events-auto">
          <PickRegionStatus failure={failure} />
        </div>
      </div>
    </>
  );
}

function SelectTargetBoard({
  state,
  select,
}: {
  state: ClientGameState;
  select: (regionId: string | null) => void;
}) {
  const { select: selectTarget, failure } = useSelectTarget(state.game_id, deadlineIdOf(state));
  return (
    <>
      <MapBoard
        state={state}
        onSelect={(regionId) => {
          select(regionId);
          selectTarget(regionId);
        }}
      />
      <div className="pointer-events-none absolute inset-x-0 bottom-0 p-3">
        <div className="pointer-events-auto">
          <SelectTargetStatus failure={failure} />
        </div>
      </div>
    </>
  );
}
