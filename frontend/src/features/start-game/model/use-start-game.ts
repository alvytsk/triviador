import { useMutation } from "@tanstack/react-query";
import { apiSend, gameSnapshotSchema } from "@/shared/api";

/**
 * `POST /api/games/{id}/start` answers with a `GameSnapshot`, the same as
 * create and join — and the same reasoning applies: writing it into
 * `["game", id]` from here would make this mutation a second writer of that
 * cache, which the lint gate on `@/app/dispatcher` forbids. Unlike create
 * and join there is nowhere to navigate to (the caller is already on the
 * game's own page), so there is no `onSuccess` at all: the socket delivers
 * the started state as a `game.update` a moment later, over the one path
 * that has to work regardless.
 */
export function useStartGame() {
  return useMutation({
    mutationFn: (gameId: string) =>
      apiSend(`/api/games/${gameId}/start`, gameSnapshotSchema, undefined),
  });
}
