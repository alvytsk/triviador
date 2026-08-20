import { QueryClientProvider } from "@tanstack/react-query";
import { type RenderResult, render } from "@testing-library/react";
import type { ReactNode } from "react";
import { createQueryClient } from "@/app/query-client";
import { SocketProvider } from "@/app/socket-provider";
import { createSocketClient, type SocketClient } from "@/shared/api";
import { fakeSocketFactory } from "./fake-socket";

export interface AppHarness extends RenderResult {
  queryClient: ReturnType<typeof createQueryClient>;
  socket: ReturnType<typeof fakeSocketFactory>;
  client: SocketClient;
}

/**
 * Every component test renders through the real providers, so a component
 * that quietly depends on one fails here rather than in a browser. The socket
 * is real — `createSocketClient` with a fake transport — so the dispatcher,
 * the schemas and the frame encoding are all exercised rather than mocked
 * away, which is where the bugs actually are.
 */
export function renderWithApp(
  ui: ReactNode,
  options: { seed?: (harness: Omit<AppHarness, keyof RenderResult>) => void } = {},
): AppHarness {
  const queryClient = createQueryClient();
  const socket = fakeSocketFactory();
  const client = createSocketClient({ url: "/ws", socketFactory: socket.factory });
  options.seed?.({ queryClient, socket, client });

  // `wrapper`, not a hand-nested tree: `RenderResult.rerender` re-invokes
  // whatever was passed as `wrapper` around the new element, but it does not
  // know about JSX nested directly around the first `ui` argument. A test
  // that calls `harness.rerender(<Watcher .../>)` needs the providers back
  // every time, not just on the first render.
  function Wrapper({ children }: { children: ReactNode }) {
    return (
      <QueryClientProvider client={queryClient}>
        <SocketProvider enabled client={client}>
          {children}
        </SocketProvider>
      </QueryClientProvider>
    );
  }

  const result = render(ui, { wrapper: Wrapper });
  return { ...result, queryClient, socket, client };
}
