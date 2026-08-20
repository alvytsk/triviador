import { queryOptions } from "@tanstack/react-query";
import { z } from "zod";
import { apiFetch, mapSummarySchema } from "@/shared/api";
import { mapsKey } from "../model/keys";

/** The map choice on the create-game panel. There is no `/api/presets`
 *  (that repository is read-only and unexposed until Plan 7), so this is
 *  the only server-driven choice the panel offers. */
export function mapsQueryOptions() {
  return queryOptions({
    queryKey: mapsKey(),
    queryFn: () => apiFetch("/api/maps", z.array(mapSummarySchema)),
  });
}
