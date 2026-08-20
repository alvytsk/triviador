import { describe, expect, it } from "vitest";
import { gameState, player, territory } from "../../../../testing/factories";
import { ownershipOf } from "./ownership";

describe("ownershipOf", () => {
  const state = gameState({
    players: [player(), player({ player_id: "u2", display_name: "Petra", seat: 1 })],
    territories: [
      territory({ region_id: "praha", owner_id: null }),
      territory({
        region_id: "plzensky",
        owner_id: "u1",
        kind: "base",
        base_owner_id: "u1",
        base_hp: 2,
      }),
      territory({ region_id: "liberecky", owner_id: "u2" }),
    ],
  });

  it("maps an owner to a seat, because colour is derived from seat and nothing else", () => {
    expect(ownershipOf(state).get("liberecky")?.ownerSeat).toBe(1);
  });

  it("leaves a free region with no seat", () => {
    expect(ownershipOf(state).get("praha")?.ownerSeat).toBeNull();
  });

  it("carries a base and its remaining hit points", () => {
    expect(ownershipOf(state).get("plzensky")).toEqual({ ownerSeat: 0, isBase: true, baseHp: 2 });
  });

  it("has an entry for every territory the projection sent and no others", () => {
    expect([...ownershipOf(state).keys()].sort()).toEqual(["liberecky", "plzensky", "praha"]);
  });
});
