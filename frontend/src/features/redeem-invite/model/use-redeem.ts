import { useMutation, useQueryClient } from "@tanstack/react-query";
import { meKey } from "@/entities/game";
import { apiSend, meSchema, type RedeemRequest } from "@/shared/api";

export function useRedeem(onDone: () => void) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (body: RedeemRequest) => apiSend("/api/auth/redeem", meSchema, body),
    onSuccess: (me) => {
      // Seed rather than invalidate: the response *is* `/api/auth/me`'s body,
      // and a refetch here would race the socket opening on the next render.
      queryClient.setQueryData(meKey(), me);
      onDone();
    },
  });
}
