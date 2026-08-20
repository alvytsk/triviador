import { createMemoryHistory, createRouter, RouterProvider } from "@tanstack/react-router";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { HttpResponse, http } from "msw";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { FakeSocket } from "../../testing/fake-socket";
import { server } from "../../testing/msw";
import { Providers } from "./app-providers";
import { createQueryClient } from "./query-client";
import { routeTree } from "./routes/routeTree.gen";

const ME = { user_id: "u1", username: "alexey", display_name: "Alexey", role: "player" };

/**
 * Every render below goes through the real `Providers` from
 * `app-providers.tsx` — the same tree `main.tsx` mounts — rather than a
 * hand-rolled `QueryClientProvider`. That means `SocketWhenSignedIn` also
 * mounts and issues its own `GET /api/auth/me` on every render here, which
 * is why every test below installs a handler for it: the point of using
 * the real providers is that a future provider dependency in `LoginPage`
 * or `SignInForm` fails *here*, not in a browser.
 *
 * `Providers` does not accept an injected socket client — unlike
 * `renderWithApp`, which is only usable below `app/` — so once sign-in
 * flips `["me"]` to non-null, `SocketProvider` opens a *real* `WebSocket`
 * to a host nothing is listening on. That used to "work" only because
 * `SocketProvider`'s cleanup effect fired a few milliseconds before the
 * OS's connection refusal landed — a race, not a guarantee, and the kind
 * that goes from green to intermittently red the day a slower CI box or
 * one extra `await` shifts that timing. Stubbing the global `WebSocket`
 * with `FakeSocket` (already built for exactly this — see
 * `testing/fake-socket.ts` — and constructor-compatible with `WebSocket`)
 * removes the race entirely without touching any production code. Tasks
 * 12 and 14 mount signed-in app trees the same way this file does; copy
 * this stub along with the pattern rather than re-discovering the race.
 */
beforeEach(() => {
  vi.stubGlobal("WebSocket", FakeSocket);
});

afterEach(() => {
  vi.unstubAllGlobals();
});

function meUnauthenticated() {
  return http.get("/api/auth/me", () =>
    HttpResponse.json(
      { code: "unauthenticated", message: "no session", details: null },
      { status: 401 },
    ),
  );
}

/** The real route tree, the real `LoginPage`, a memory history seeded at a
 *  given URL — this is `search.next` exercised end to end rather than just
 *  at the schema (`login-search.test.ts` already covers the schema's own
 *  attack vectors exhaustively; this file is only about what the app does
 *  with a value the schema let through, or refused to). */
function renderRouter(initial: string) {
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

describe("the /login route's next, navigated end to end", () => {
  it("lands on next after a successful sign-in when next is a valid relative path", async () => {
    server.use(
      meUnauthenticated(),
      http.post("/api/auth/login", () => HttpResponse.json(ME)),
    );
    const { router } = renderRouter("/login?next=%2Fsomewhere");

    await waitFor(() => expect(screen.getByLabelText("USERNAME")).toBeInTheDocument());
    await userEvent.type(screen.getByLabelText("USERNAME"), "alexey");
    await userEvent.type(screen.getByLabelText("PASSWORD"), "hunter2hunter2");
    await userEvent.click(screen.getByRole("button", { name: /sign in/i }));

    await waitFor(() => expect(router.state.location.pathname).toBe("/somewhere"));
  });

  it("lands on / after a successful sign-in when there is no next at all", async () => {
    server.use(
      meUnauthenticated(),
      http.post("/api/auth/login", () => HttpResponse.json(ME)),
    );
    const { router } = renderRouter("/login");

    await waitFor(() => expect(screen.getByLabelText("USERNAME")).toBeInTheDocument());
    await userEvent.type(screen.getByLabelText("USERNAME"), "alexey");
    await userEvent.type(screen.getByLabelText("PASSWORD"), "hunter2hunter2");
    await userEvent.click(screen.getByRole("button", { name: /sign in/i }));

    // `search.next ?? "/"` — the fallback branch. The `_authed` guard at
    // `/` re-checks `/api/auth/me`, but the sign-in mutation already seeded
    // that exact cache entry (`useSignIn`'s `onSuccess`), so the guard finds
    // it without a second round trip and lets the match through rather than
    // bouncing back to `/login`.
    await waitFor(() => expect(router.state.location.pathname).toBe("/"));
  });

  it("does not navigate off-origin when next fails validation", async () => {
    // `loginSearchSchema` rejects this before the route ever matches
    // successfully — `LoginPage` never mounts, `SignInForm.onDone` never
    // runs, and nothing calls `navigate({ to: "https://evil.example/" })`.
    // What proves that here is structural, not a screenshot of the
    // router's own error UI: the location never leaves `/login`, and the
    // sign-in form — the only thing in this app that could call `navigate`
    // with `next` — never rendered.
    server.use(meUnauthenticated());
    const { router } = renderRouter("/login?next=https%3A%2F%2Fevil.example%2F");

    await waitFor(() =>
      expect(router.state.matches.find((m) => m.routeId === "/login")?.status).toBe("error"),
    );
    expect(router.state.location.pathname).toBe("/login");
    expect(screen.queryByLabelText("USERNAME")).not.toBeInTheDocument();
  });
});
