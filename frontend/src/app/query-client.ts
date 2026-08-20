import { QueryClient } from "@tanstack/react-query";
import { ApiFetchError } from "@/shared/api";

/**
 * §9.3's defaults, in the one place they can be true everywhere.
 *
 * `staleTime: Infinity` and both refetch switches off because the socket is
 * the refresh mechanism. A background refetch racing a `game.update` is
 * exactly the race `writeGame`'s seq comparison exists to survive — but the
 * cheapest way to survive a race is not to start one.
 */
export function createQueryClient(): QueryClient {
  return new QueryClient({
    defaultOptions: {
      queries: {
        staleTime: Number.POSITIVE_INFINITY,
        refetchOnWindowFocus: false,
        refetchOnReconnect: false,
        retry: (failureCount, error) => {
          // Retrying a refusal is noise: the server has answered, and it will
          // answer the same way. Only a transport failure is worth a retry,
          // and only twice.
          if (error instanceof ApiFetchError && error.kind === "envelope") return false;
          return failureCount < 2;
        },
      },
      mutations: { retry: false },
    },
  });
}
