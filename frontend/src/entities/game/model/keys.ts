/** Every cache key in the application. Written once so that a typo cannot
 *  produce a second, silently-empty cache entry — the failure mode where the
 *  socket updates `["game","g1"]` and a component reads `["games","g1"]`.
 *
 *  `["map", mapId]` is *not* defined here: `entities/map/model/keys.ts` owns
 *  it, because that slice caches the parsed map and nothing in this slice
 *  should key a second, differently-shaped query under the same array. */
export const meKey = () => ["me"] as const;
export const lobbyKey = () => ["lobby"] as const;
export const gameKey = (gameId: string) => ["game", gameId] as const;
export const mapsKey = () => ["maps"] as const;
export const presetsKey = () => ["presets"] as const;
