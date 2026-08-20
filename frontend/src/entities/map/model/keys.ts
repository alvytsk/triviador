/**
 * Deliberately the same shape as `entities/game`'s `mapKey` — `["map",
 * mapId]` — so the two live in the same conceptual cache slot even though
 * `fsd/forbidden-imports` (recommended FSD, entities may not cross-import
 * each other's public API without the `@x` escape hatch) means this slice
 * cannot import that binding directly. TanStack Query compares keys
 * structurally, not by identity, so a literal match here is the same key.
 */
export const mapKey = (mapId: string) => ["map", mapId] as const;
