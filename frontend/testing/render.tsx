import { type QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { createMemoryHistory, createRouter, RouterContextProvider } from "@tanstack/react-router";
import { type RenderResult, render } from "@testing-library/react";
import type { ReactNode } from "react";
import { afterEach, beforeEach, vi } from "vitest";
import { createEventBus, type EventBus } from "@/app/event-bus";
import { createQueryClient } from "@/app/query-client";
import { routeTree } from "@/app/routes/routeTree.gen";
import { SocketProvider } from "@/app/socket-provider";
import { createSocketClient, type SocketClient } from "@/shared/api";
import { FakeSocket, fakeSocketFactory } from "./fake-socket";

/**
 * `createSocketClient`'s default factory is `new WebSocket(url)`. This file
 * always injects a fake `client` below, so that default path is never
 * exercised through `renderWithApp` today — but `login-next-navigation.test.tsx`
 * proved the failure mode this guards against anyway: a signed-in tree that
 * *doesn't* inject a client (the real `Providers`, which `SocketWhenSignedIn`
 * drives) opens a genuine `WebSocket` to a host nothing listens on, racing
 * this harness's own unmount. Stubbing the global once, here, means every
 * test that reaches this module gets the safe default without having to
 * know that race exists, rather than each future signed-in test rediscovering
 * it the way that file's own comment describes.
 */
beforeEach(() => {
  vi.stubGlobal("WebSocket", FakeSocket);
});

afterEach(() => {
  vi.unstubAllGlobals();
});

// A plain (non-generic) wrapper, not `createRouter` applied inline at every
// call site: `ReturnType<typeof createRouter>` on the generic function
// itself resolves against its *default* type parameters, not the concrete
// `typeof routeTree` this app actually uses — a real, narrower mismatch that
// `tsc --noEmit` catches under `exactOptionalPropertyTypes` the moment
// `AppHarness.router` is typed that way. Wrapping the one call this file
// ever makes in a non-generic function fixes that: `createTestRouter`'s own
// return type is a single concrete type, resolved once, right here.
function createTestRouter(queryClient: QueryClient, initialPath: string) {
  const history = createMemoryHistory({ initialEntries: [initialPath] });
  return createRouter({ routeTree, context: { queryClient }, history });
}

export interface AppHarness extends RenderResult {
  queryClient: ReturnType<typeof createQueryClient>;
  socket: ReturnType<typeof fakeSocketFactory>;
  client: SocketClient;
  router: ReturnType<typeof createTestRouter>;
  /** The real `EventBus` `SocketProvider`'s dispatcher emits narration onto
   *  — see `SocketProvider`'s `bus` prop for why this exists: it is the one
   *  way a test outside `app/` can observe whether narration actually fired
   *  for a given message, real production dispatcher and all. */
  bus: EventBus;
}

/**
 * Every component test renders through the real providers, so a component
 * that quietly depends on one fails here rather than in a browser. The socket
 * is real — `createSocketClient` with a fake transport — so the dispatcher,
 * the schemas and the frame encoding are all exercised rather than mocked
 * away, which is where the bugs actually are.
 *
 * The router is real for the same reason. Task 10's whole architectural
 * claim — a mutation hands off to `useNavigate()` instead of writing the
 * query cache directly (`features/create-game`, `features/join-game`) — is
 * unverifiable against no router at all: `useNavigate()` resolves `router`
 * from context, and *before* this, that context was always empty here, so
 * the function it returns would throw the moment `onSuccess` actually called
 * it. Nothing caught that, because every existing mutation test either never
 * clicked the button or clicked it under an error mock.
 *
 * `RouterContextProvider` — not the full `RouterProvider` — on purpose: the
 * full one renders whatever the *current route* matches, discarding `ui`
 * entirely, which would break every test in this suite that renders an
 * arbitrary component rather than a page. `RouterContextProvider` only
 * supplies a real, working router through context and renders `children`
 * (`ui`) exactly as before. This is not a lesser router — `router.navigate`
 * pushes to `history` and then calls `router.load()` itself whenever nothing
 * else is already subscribed to that history (`router-core`'s
 * `commitLocation`), which is exactly this harness's situation since the
 * route tree's own `<Matches>`/`<Transitioner>` never mount. A test that
 * cares where a mutation navigated reads `harness.router.state.location`
 * after a `waitFor`, the same way `login-next-navigation.test.tsx` does
 * against the real `RouterProvider`.
 *
 * `initialPath` seeds the memory history. It defaults to `/` — safe because
 * constructing the router does not itself run any route's `beforeLoad`;
 * that only happens once something actually navigates.
 */
export function renderWithApp(
  ui: ReactNode,
  options: {
    seed?: (harness: Omit<AppHarness, keyof RenderResult>) => void;
    initialPath?: string;
  } = {},
): AppHarness {
  const queryClient = createQueryClient();
  const socket = fakeSocketFactory();
  const client = createSocketClient({ url: "/ws", socketFactory: socket.factory });
  const router = createTestRouter(queryClient, options.initialPath ?? "/");
  const bus = createEventBus();
  options.seed?.({ queryClient, socket, client, router, bus });

  // `wrapper`, not a hand-nested tree: `RenderResult.rerender` re-invokes
  // whatever was passed as `wrapper` around the new element, but it does not
  // know about JSX nested directly around the first `ui` argument. A test
  // that calls `harness.rerender(<Watcher .../>)` needs the providers back
  // every time, not just on the first render.
  function Wrapper({ children }: { children: ReactNode }) {
    return (
      <QueryClientProvider client={queryClient}>
        <SocketProvider enabled client={client} bus={bus}>
          <RouterContextProvider router={router}>{children}</RouterContextProvider>
        </SocketProvider>
      </QueryClientProvider>
    );
  }

  const result = render(ui, { wrapper: Wrapper });
  return { ...result, queryClient, socket, client, router, bus };
}
