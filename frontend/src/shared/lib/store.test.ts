import { describe, expect, it } from "vitest";
import { useBoardStore } from "./store";

describe("the board store", () => {
  it("holds exactly the five keys §9.2 allows", () => {
    const state = useBoardStore.getState();
    const data = Object.entries(state)
      .filter(([, value]) => typeof value !== "function")
      .map(([key]) => key)
      .sort();
    // If this fails because you added a key, read §9.2 before you change it:
    // territory owner, score, round, current question and timer are server
    // state and belong in the query cache.
    expect(data).toEqual(["mapPan", "mapZoom", "openPanel", "selectedRegionId", "soundEnabled"]);
  });

  it("clears a selection", () => {
    useBoardStore.getState().select("praha");
    expect(useBoardStore.getState().selectedRegionId).toBe("praha");
    useBoardStore.getState().select(null);
    expect(useBoardStore.getState().selectedRegionId).toBeNull();
  });
});
