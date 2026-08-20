import { queryOptions } from "@tanstack/react-query";
import { z } from "zod";
import { apiFetch, mapDetailSchema, mapSummarySchema } from "@/shared/api";
import { mapKey, mapsKey } from "../model/keys";

/** The map choice on the create-game panel. There is no `/api/presets`
 *  (that repository is read-only and unexposed until Plan 7), so this is
 *  the only server-driven choice the panel offers. */
export function mapsQueryOptions() {
  return queryOptions({
    queryKey: mapsKey(),
    queryFn: () => apiFetch("/api/maps", z.array(mapSummarySchema)),
  });
}

/** One map's regions and its `svg_url` — Task 11's `MapBoard` and Task 12's
 *  game screen consume this; nothing in the lobby does yet. */
export function mapDetailQueryOptions(mapId: string) {
  return queryOptions({
    queryKey: mapKey(mapId),
    queryFn: () => apiFetch(`/api/maps/${mapId}`, mapDetailSchema),
  });
}
