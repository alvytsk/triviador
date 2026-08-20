/**
 * The one definition of the map cache key. It caches the *parsed* map
 * (`ParsedMap`, via `mapQueryOptions`) — not the raw `MapDetail` the REST
 * endpoint returns — so anyone adding a second query for the unparsed
 * detail must give it a different key rather than colliding here.
 */
export const mapKey = (mapId: string) => ["map", mapId] as const;
