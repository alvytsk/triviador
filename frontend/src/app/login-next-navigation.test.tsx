import { QueryClientProvider } from "@tanstack/react-query";
import { createMemoryHistory, createRouter, RouterProvider } from "@tanstack/react-router";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { HttpResponse, http } from "msw";
import { describe, expect, it } from "vitest";
import { server } from "../../testing/msw";
import { createQueryClient } from "./query-client";
import { routeTree } from "./routes/routeTree.gen";

const ME = { user_id: "u1", username: "alexey", display_name: "Alexey", role: "player" };

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
    <QueryClientProvider client={queryClient}>
      <RouterProvider router={router} />
    </QueryClientProvider>,
  );
  return { router, queryClient, ...view };
}

describe("the /login route's next, navigated end to end", () => {
  it("lands on next after a successful sign-in when next is a valid relative path", async () => {
    server.use(http.post("/api/auth/login", () => HttpResponse.json(ME)));
    const { router } = renderRouter("/login?next=%2Fsomewhere");

    await waitFor(() => expect(screen.getByLabelText("USERNAME")).toBeInTheDocument());
    await userEvent.type(screen.getByLabelText("USERNAME"), "alexey");
    await userEvent.type(screen.getByLabelText("PASSWORD"), "hunter2hunter2");
    await userEvent.click(screen.getByRole("button", { name: /sign in/i }));

    await waitFor(() => expect(router.state.location.pathname).toBe("/somewhere"));
  });

  it("does not navigate off-origin when next fails validation", async () => {
    // `loginSearchSchema` rejects this before the route ever matches
    // successfully — `LoginPage` never mounts, `SignInForm.onDone` never
    // runs, and nothing calls `navigate({ to: "https://evil.example/" })`.
    // What proves that here is structural, not a screenshot of the
    // router's own error UI: the location never leaves `/login`, and the
    // sign-in form — the only thing in this app that could call `navigate`
    // with `next` — never rendered.
    const { router } = renderRouter("/login?next=https%3A%2F%2Fevil.example%2F");

    await waitFor(() =>
      expect(router.state.matches.find((m) => m.routeId === "/login")?.status).toBe("error"),
    );
    expect(router.state.location.pathname).toBe("/login");
    expect(screen.queryByLabelText("USERNAME")).not.toBeInTheDocument();
  });
});
