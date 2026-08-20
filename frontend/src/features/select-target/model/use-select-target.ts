import { useCommand } from "@/shared/api";

/**
 * Decision 10's shape for `select_attack_target` — identical to
 * `usePickRegion`, one type literal apart. `deadlineId` is `deadlineIdOf(state)`
 * at the call site; `null` (no open targeting window) makes `select` a no-op.
 */
export function useSelectTarget(gameId: string, deadlineId: number | null) {
  const { send, pending, failure } = useCommand();
  return {
    select: (regionId: string) => {
      if (deadlineId === null) return;
      send((command_id) => ({
        type: "select_attack_target",
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
