import { useMutation } from "@tanstack/react-query";
import { useNavigate } from "@tanstack/react-router";
import { apiSend, gameSnapshotSchema } from "@/shared/api";

/**
 * Same reasoning as `useCreateGame`: `POST /api/games/{id}/join` answers
 * with a `GameSnapshot`, and this mutation must not be the second writer of
 * `["game", id]` — it navigates to `/games/{id}` and lets that route's
 * loader fetch the same snapshot through `writeGame`, the one merge rule.
 *
 * Also doubles as "rejoin": the lobby summary carries no participant list,
 * so a game whose `status` is not `"lobby"` cannot be told apart from one
 * you were never in — both offer this same join call, and the server is
 * the one that knows whether you belong.
 */
export function useJoinGame() {
  const navigate = useNavigate();
  return useMutation({
    mutationFn: (gameId: string) =>
      apiSend(`/api/games/${gameId}/join`, gameSnapshotSchema, undefined),
    onSuccess: (snapshot) =>
      navigate({ to: "/games/$gameId", params: { gameId: snapshot.state.game_id } }),
  });
}
