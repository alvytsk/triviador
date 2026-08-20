import { create } from "zustand";

/**
 * §9.2's table, exactly. Five keys, and the "explicitly not" column is the
 * important half: territory owner, score, round, the current question and the
 * timer are *never* here. Every one of them is server state, and a copy of
 * server state in a client store is a copy that will be stale at the worst
 * possible moment.
 */
export interface BoardState {
  selectedRegionId: string | null;
  mapZoom: number;
  mapPan: { x: number; y: number };
  openPanel: "none" | "log" | "rules";
  soundEnabled: boolean;
  select(regionId: string | null): void;
  setZoom(zoom: number): void;
  setPan(pan: { x: number; y: number }): void;
  setPanel(panel: BoardState["openPanel"]): void;
  toggleSound(): void;
}

export const useBoardStore = create<BoardState>((set) => ({
  selectedRegionId: null,
  mapZoom: 1,
  mapPan: { x: 0, y: 0 },
  openPanel: "none",
  soundEnabled: true,
  select: (selectedRegionId) => set({ selectedRegionId }),
  setZoom: (mapZoom) => set({ mapZoom }),
  setPan: (mapPan) => set({ mapPan }),
  setPanel: (openPanel) => set({ openPanel }),
  toggleSound: () => set((state) => ({ soundEnabled: !state.soundEnabled })),
}));
