import { deadlineOf, turnKindOf } from "@/entities/game";
import type { ClientGameState } from "@/shared/api";
import { TimerBar } from "@/shared/ui";

// The two turn kinds that ask for a click on the map rather than an answer.
// The command itself is sent from `<GameStage>` (§9.5's map, not this dock)
// — see that widget's doc comment for why. This is only the instruction.
const MAP_INSTRUCTION: Partial<Record<string, string>> = {
  expansion_picking: "Pick a region on the map.",
  battle_target_select: "Choose a target on the map.",
};

/**
 * The bottom band of §9.5's geometry — `grow`, filling whatever the fixed
 * player strip and stage above it leave. `<BoardView>` renders this only
 * when the open turn has no question (`questionOf(state) === null`); a
 * question turn renders `<QuestionDock>` instead, which is Task 13's answer
 * form and does not go through here. What is left for this component is the
 * pick/target instruction and, for everything else (warmup, no turn), the
 * phase readout.
 */
export function TurnDock({ state }: { state: ClientGameState }) {
  const kind = turnKindOf(state);
  const instruction = kind !== null ? MAP_INSTRUCTION[kind] : undefined;

  return (
    <div className="flex grow items-center justify-between border-t border-line bg-panel px-6">
      <span className="text-[13px] font-semibold uppercase tracking-[0.14em] text-ink-dim">
        {instruction ??
          `Round ${state.round_no} · ${state.phase}${kind !== null ? ` — ${kind.replace(/_/g, " ")}` : ""}`}
      </span>
      <TimerBar deadlineAt={deadlineOf(state)} />
    </div>
  );
}
