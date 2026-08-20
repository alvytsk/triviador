import { queryOptions } from "@tanstack/react-query";
import { apiFetch, mapDetailSchema } from "@/shared/api";
import { mapKey } from "../model/keys";
import { type ParsedMap, parseMapSvg } from "../model/parse";

/**
 * Two fetches behind one key: the detail (which names the SVG's URL and the
 * region ids the SVG must match) and the SVG itself. Cached forever — a map
 * is immutable for the life of a game, and `games.map_sha256` is what
 * guarantees that.
 *
 * The SVG is fetched with `fetch`, not `apiFetch`: it is served by Caddy from
 * `data/maps` (Spec 1B §10.2) and is not an API endpoint, so an error from it
 * is not an envelope and must not be parsed as one.
 */
export function mapQueryOptions(mapId: string) {
  return queryOptions({
    queryKey: mapKey(mapId),
    queryFn: async (): Promise<ParsedMap> => {
      const detail = await apiFetch(`/api/maps/${mapId}`, mapDetailSchema);
      const response = await fetch(detail.svg_url);
      if (!response.ok) {
        throw new Error(`map ${mapId}: ${detail.svg_url} answered ${response.status}`);
      }
      return parseMapSvg(
        await response.text(),
        detail.regions.map((r) => r.region_id),
      );
    },
    staleTime: Number.POSITIVE_INFINITY,
    retry: false,
  });
}
