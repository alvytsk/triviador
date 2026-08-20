import { useMutation } from "@tanstack/react-query";
import { useNavigate } from "@tanstack/react-router";
import { apiSend, type CreateGameRequest, gameSnapshotSchema } from "@/shared/api";

/**
 * `POST /api/games` answers with a `GameSnapshot` — the same shape
 * `game.snapshot` carries over the socket. Writing it into `["game", id]`
 * from here would make this mutation a second writer of that cache, which
 * the lint gate on `@/app/dispatcher` forbids (only `writeGame` may touch
 * that key, and only from `app/`). Navigating to `/games/{id}` instead lets
 * that route's own loader fetch the identical snapshot through the one
 * merge rule — a few extra milliseconds on a LAN, and one writer instead of
 * two.
 */
export function useCreateGame() {
  const navigate = useNavigate();
  return useMutation({
    mutationFn: (body: CreateGameRequest) => apiSend("/api/games", gameSnapshotSchema, body),
    onSuccess: (snapshot) =>
      navigate({ to: "/games/$gameId", params: { gameId: snapshot.state.game_id } }),
  });
}
