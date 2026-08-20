import { type CommandFailure, useCommand } from "@/shared/api";

/**
 * `{ type: "surrender", command_id, game_id }` — no `deadline_id` and no
 * `payload`, because surrender is not windowed (`surrenderFrameSchema`'s own
 * description says as much: "carries nothing"). Decision 10's shape still
 * applies otherwise: build a frame, send it, mark it pending, wait — the
 * confirmation that makes this irreversible action deliberate lives in
 * `<SurrenderButton>`, not here.
 */
export function useSurrender(gameId: string) {
  const { send, pending, failure } = useCommand();
  return {
    surrender: () => {
      send((command_id) => ({
        type: "surrender",
        command_id,
        game_id: gameId,
      }));
    },
    isSending: pending.size > 0,
    failure,
  };
}

export type { CommandFailure };
