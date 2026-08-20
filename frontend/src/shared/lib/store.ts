import { create } from "zustand";

/**
 * §9.2's table, exactly. Five keys, and the "explicitly not" column is the
 * important half: territory owner, score, round, the current question and the
 * timer are *never* here. Every one of them is server state, and a copy of
 * server state in a client store is a copy that will be stale at the worst
 * possible moment.
 *
 * `mapZoom`/`mapPan`/`openPanel`/`soundEnabled` are §9.2's reserved slots for
 * UI this branch has not built yet (pan/zoom controls, a log/rules panel,
 * a sound toggle) — kept, and locked in by `store.test.ts`'s "exactly the
 * five keys" assertion, so a future task has the slot without re-litigating
 * §9.2. Their setters are not: nothing in this branch calls `setZoom`,
 * `setPan`, `setPanel` or `toggleSound`, and an unused setter sitting next to
 * three wired-up ones reads as "this is connected to something" when it
 * is not. Deleted rather than kept as a decoy; the field stays initialised
 * to its default and a future task adds back exactly the setter it needs.
 */
export interface BoardState {
  selectedRegionId: string | null;
  mapZoom: number;
  mapPan: { x: number; y: number };
  openPanel: "none" | "log" | "rules";
  soundEnabled: boolean;
  select(regionId: string | null): void;
}

export const useBoardStore = create<BoardState>((set) => ({
  selectedRegionId: null,
  mapZoom: 1,
  mapPan: { x: 0, y: 0 },
  openPanel: "none",
  soundEnabled: true,
  select: (selectedRegionId) => set({ selectedRegionId }),
}));
