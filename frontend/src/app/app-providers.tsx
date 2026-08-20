import { type QueryClient, QueryClientProvider, useQuery } from "@tanstack/react-query";
import type { ReactNode } from "react";
import { meQueryOptions } from "@/entities/game";
import { AppErrorBoundary } from "./error-boundary";
import { SocketProvider } from "./socket-provider";

/** The socket is opened only once there is a session to open it with — an
 *  unauthenticated `/ws` handshake is closed with 4401 by the server, and
 *  reconnect-storming the login screen is not a good look. */
function SocketWhenSignedIn({ children }: { children: ReactNode }) {
  const me = useQuery(meQueryOptions());
  return <SocketProvider enabled={me.data != null}>{children}</SocketProvider>;
}

export function Providers({
  queryClient,
  children,
}: {
  queryClient: QueryClient;
  children: ReactNode;
}) {
  return (
    <QueryClientProvider client={queryClient}>
      <AppErrorBoundary>
        <SocketWhenSignedIn>{children}</SocketWhenSignedIn>
      </AppErrorBoundary>
    </QueryClientProvider>
  );
}
