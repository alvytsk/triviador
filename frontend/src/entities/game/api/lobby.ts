import { queryOptions } from "@tanstack/react-query";
import { z } from "zod";
import { apiFetch, lobbyGameSummarySchema } from "@/shared/api";
import { lobbyKey } from "../model/keys";

/**
 * §9.3's pattern at its simplest: `GET /api/games` fills the list before
 * the socket has said anything, and `["lobby"]` is kept fresh after that by
 * the dispatcher writing every `lobby.snapshot` / `lobby.update` straight
 * into this same key — no merge rule needed here, because a lobby summary
 * carries no `seq` to compare, unlike a game.
 */
export function lobbyQueryOptions() {
  return queryOptions({
    queryKey: lobbyKey(),
    queryFn: () => apiFetch("/api/games", z.array(lobbyGameSummarySchema)),
  });
}
