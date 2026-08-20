import { type CommandFailure, useCommand } from "@/shared/api";

/**
 * Decision 10's shape for `pick_region`: build a frame, send it, mark it
 * pending, wait. `deadlineId` comes from `deadlineIdOf(state)` at the call
 * site and is never invented — a `null` deadline (no open picking window)
 * makes `pick` a no-op rather than sending a frame the server has nothing to
 * correlate.
 */
export function usePickRegion(gameId: string, deadlineId: number | null) {
  const { send, pending, failure } = useCommand();
  return {
    pick: (regionId: string) => {
      if (deadlineId === null) return;
      send((command_id) => ({
        type: "pick_region",
        command_id,
        game_id: gameId,
        deadline_id: deadlineId,
        payload: { region_id: regionId },
      }));
    },
    isSending: pending.size > 0,
    failure,
  };
}

export type { CommandFailure };
