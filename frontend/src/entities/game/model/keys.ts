/** Every cache key in the application. Written once so that a typo cannot
 *  produce a second, silently-empty cache entry — the failure mode where the
 *  socket updates `["game","g1"]` and a component reads `["games","g1"]`. */
export const meKey = () => ["me"] as const;
export const lobbyKey = () => ["lobby"] as const;
export const gameKey = (gameId: string) => ["game", gameId] as const;
export const mapKey = (mapId: string) => ["map", mapId] as const;
export const mapsKey = () => ["maps"] as const;
