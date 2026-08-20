import type { ClientGameState, ClientPlayer } from "@/shared/api";

/** Seat order, which is turn order at the table and is stable for the whole
 *  game — unlike `turn_order`, which the server rotates. */
export function orderedPlayers(state: ClientGameState): readonly ClientPlayer[] {
  return [...state.players].sort((a, b) => a.seat - b.seat);
}
