import type { ClientGameState, ClientPlayer } from "@/shared/api";
import { seatVar } from "@/shared/config";

/** §8.1: appearance is derived. This returns a CSS `var(...)` reference, not
 *  a colour — nothing in the app ever holds a hex value for a player. */
export function seatColorOf(state: ClientGameState, playerId: string | null): string | null {
  if (playerId === null) return null;
  const player = state.players.find((p) => p.player_id === playerId);
  return player === undefined ? null : seatVar(player.seat);
}

/** Seat order, which is turn order at the table and is stable for the whole
 *  game — unlike `turn_order`, which the server rotates. */
export function orderedPlayers(state: ClientGameState): readonly ClientPlayer[] {
  return [...state.players].sort((a, b) => a.seat - b.seat);
}
