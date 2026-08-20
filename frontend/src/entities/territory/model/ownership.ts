import type { ClientGameState } from "@/shared/api";

export interface TerritoryView {
  ownerSeat: number | null;
  isBase: boolean;
  baseHp: number | null;
}

/**
 * The map's whole input, in one pass.
 *
 * Owner ids become *seats* here and nowhere else: §8.1 says fill comes from
 * `territories[id].owner_id` mapped to a per-seat custom property, and doing
 * that mapping in the renderer would mean every region re-scanning the player
 * list on every frame.
 */
export function ownershipOf(state: ClientGameState): ReadonlyMap<string, TerritoryView> {
  const seatOf = new Map(state.players.map((p) => [p.player_id, p.seat]));
  const view = new Map<string, TerritoryView>();
  for (const territory of state.territories) {
    view.set(territory.region_id, {
      ownerSeat: territory.owner_id === null ? null : (seatOf.get(territory.owner_id) ?? null),
      isBase: territory.kind === "base",
      baseHp: territory.base_hp,
    });
  }
  return view;
}
