import { deadlineOf, turnKindOf } from "@/entities/game";
import type { ClientGameState } from "@/shared/api";
import { TimerBar } from "./timer-bar";

/**
 * The bottom band of §9.5's geometry — `grow`, filling whatever the fixed
 * player strip and stage above it leave. Task 13 fills it with the answer
 * form, the pick/target grid and the surrender control; for now it is the
 * phase readout and the clock, so the layout is complete even though the
 * commands are not.
 */
export function TurnDock({ state }: { state: ClientGameState }) {
  const kind = turnKindOf(state);

  return (
    <div className="flex grow items-center justify-between border-t border-line bg-panel px-6">
      <span className="text-[13px] font-semibold uppercase tracking-[0.14em] text-ink-dim">
        Round {state.round_no} · {state.phase}
        {kind !== null ? ` — ${kind.replace(/_/g, " ")}` : ""}
      </span>
      <TimerBar deadlineAt={deadlineOf(state)} />
    </div>
  );
}
