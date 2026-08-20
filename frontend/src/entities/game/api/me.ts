import { queryOptions } from "@tanstack/react-query";
import { apiFetch, meSchema } from "@/shared/api";
import { meKey } from "../model/keys";

export function meQueryOptions() {
  return queryOptions({
    queryKey: meKey(),
    queryFn: () => apiFetch("/api/auth/me", meSchema),
    // A 401 here is the answer, not a failure to retry.
    retry: false,
  });
}
