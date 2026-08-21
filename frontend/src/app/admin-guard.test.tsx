import { createMemoryHistory, createRouter, RouterProvider } from "@tanstack/react-router";
import { render, screen, waitFor } from "@testing-library/react";
import { HttpResponse, http } from "msw";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { FakeSocket } from "../../testing/fake-socket";
import { server } from "../../testing/msw";
import { Providers } from "./app-providers";
import { createQueryClient } from "./query-client";
import { routeTree } from "./routes/routeTree.gen";

const ME = { user_id: "u1", username: "alexey", display_name: "Alexey", role: "player" };

/**
 * Same reasoning as `login-next-navigation.test.tsx`: every render below
 * goes through the real `Providers`, which mounts `SocketWhenSignedIn`
 * unconditionally. Stubbing `WebSocket` with `FakeSocket` keeps a
 * successful "lets an admin in" render from racing a real (refused)
 * socket connection.
 */
beforeEach(() => {
  vi.stubGlobal("WebSocket", FakeSocket);
});

afterEach(() => {
  vi.unstubAllGlobals();
});

function meWithRole(role: "player" | "admin") {
  return http.get("/api/auth/me", () => HttpResponse.json({ ...ME, role }));
}

function meUnauthenticated() {
  return http.get("/api/auth/me", () =>
    HttpResponse.json(
      { code: "unauthenticated", message: "no session", details: null },
      { status: 401 },
    ),
  );
}

function renderAt(initial: string) {
  const queryClient = createQueryClient();
  const history = createMemoryHistory({ initialEntries: [initial] });
  const router = createRouter({ routeTree, context: { queryClient }, history });
  const view = render(
    <Providers queryClient={queryClient}>
      <RouterProvider router={router} />
    </Providers>,
  );
  return { router, queryClient, ...view };
}

describe("the /admin route's role guard", () => {
  it("sends a player away from /admin", async () => {
    server.use(meWithRole("player"));
    const { router } = renderAt("/admin/questions");
    await waitFor(() => expect(router.state.location.pathname).toBe("/"));
  });

  it("lets an admin in", async () => {
    server.use(meWithRole("admin"));
    renderAt("/admin/questions");
    await waitFor(() => expect(screen.getByRole("navigation")).toBeInTheDocument());
    expect(screen.getByRole("link", { name: /questions/i })).toBeInTheDocument();
  });

  it("sends an anonymous visitor to /login, not to /", async () => {
    // The `_authed` parent guard owns the 401 case (redirect to /login with
    // `next`). The admin guard's own role check must never run against a
    // `me` query that failed to resolve — if it did, it would read
    // `undefined.role` or bounce to `/` instead, silently swallowing
    // `_authed`'s redirect.
    server.use(meUnauthenticated());
    const { router } = renderAt("/admin/questions");
    await waitFor(() => expect(router.state.location.pathname).toBe("/login"));
  });

  it("moves an admin landing on bare /admin onward, since §9.7 has no screen there", async () => {
    server.use(meWithRole("admin"));
    const { router } = renderAt("/admin");
    await waitFor(() => expect(router.state.location.pathname).toBe("/admin/questions"));
  });
});
