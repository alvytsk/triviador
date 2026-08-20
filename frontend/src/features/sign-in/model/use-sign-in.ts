import { useMutation, useQueryClient } from "@tanstack/react-query";
import { meKey } from "@/entities/game";
import { apiSend, type LoginRequest, meSchema } from "@/shared/api";

export function useSignIn(onDone: () => void) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (body: LoginRequest) => apiSend("/api/auth/login", meSchema, body),
    onSuccess: (me) => {
      // Seed rather than invalidate: the response *is* `/api/auth/me`'s body,
      // and a refetch here would race the socket opening on the next render.
      queryClient.setQueryData(meKey(), me);
      onDone();
    },
  });
}
