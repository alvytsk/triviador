import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { HttpResponse, http } from "msw";
import { describe, expect, it } from "vitest";
import { server } from "../../../../testing/msw";
import { renderRoute } from "../../../../testing/render";

const ME_ADMIN = { user_id: "u1", username: "admin", display_name: "Admin", role: "admin" };

function withMe() {
  server.use(http.get("/api/auth/me", () => HttpResponse.json(ME_ADMIN)));
}

function user(overrides: Record<string, unknown> = {}) {
  return {
    id: "u2",
    username: "bob",
    display_name: "Bob",
    role: "player",
    is_active: true,
    ...overrides,
  };
}

describe("UsersPage", () => {
  it("loads and renders the user list through the real route", async () => {
    withMe();
    server.use(http.get("/api/admin/users", () => HttpResponse.json([user()])));

    renderRoute("/admin/users");

    expect(await screen.findByText("bob")).toBeInTheDocument();
  });

  it("promotes a user to admin and reflects the change once the list refetches", async () => {
    withMe();
    let listCalls = 0;
    server.use(
      http.get("/api/admin/users", () => {
        listCalls += 1;
        return HttpResponse.json([user({ role: listCalls === 1 ? "player" : "admin" })]);
      }),
      http.post("/api/admin/users/u2/role", () => HttpResponse.json(user({ role: "admin" }))),
    );

    renderRoute("/admin/users");

    await screen.findByText("bob");
    await userEvent.click(screen.getByRole("combobox", { name: /role for bob/i }));
    await userEvent.click(await screen.findByRole("option", { name: "Admin" }));

    expect(await screen.findByRole("combobox", { name: /role for bob/i })).toHaveTextContent(
      "Admin",
    );
  });

  it("does not let one row's stale mutation result shadow another row's fresh refetch", async () => {
    // The bug: `mutation.data` on row A survives forever once set — it is
    // cleared only by `mutation.reset()` or a fresh `mutate()` on that same
    // instance, never by `invalidateQueries` or a list refetch. So: demote
    // bob (row A) -> his row shows "Player" from his own mutation result.
    // Someone else (not this tab) promotes him back server-side. Then
    // promote carol (row B) -> that invalidates the list and it refetches,
    // handing bob fresh props that say "admin" again. Bob's row must show
    // that fresh truth, not the "player" his own stale mutation cached.
    withMe();
    let listCalls = 0;
    server.use(
      http.get("/api/admin/users", () => {
        listCalls += 1;
        // 1st call: initial load, bob is admin. 2nd: after bob's own
        // demotion invalidates, bob is player. 3rd+: after carol's mutation
        // invalidates -- by now another admin has (server-side) put bob
        // back to admin.
        const bobRole = listCalls === 1 ? "admin" : listCalls === 2 ? "player" : "admin";
        return HttpResponse.json([
          user({ id: "u2", username: "bob", role: bobRole }),
          user({ id: "u3", username: "carol", role: "player" }),
        ]);
      }),
      http.post("/api/admin/users/u2/role", () =>
        HttpResponse.json(user({ id: "u2", username: "bob", role: "player" })),
      ),
      http.post("/api/admin/users/u3/role", () =>
        HttpResponse.json(user({ id: "u3", username: "carol", role: "admin" })),
      ),
    );

    renderRoute("/admin/users");

    await screen.findByText("bob");
    expect(await screen.findByRole("combobox", { name: /role for bob/i })).toHaveTextContent(
      "Admin",
    );

    await userEvent.click(screen.getByRole("combobox", { name: /role for bob/i }));
    await userEvent.click(await screen.findByRole("option", { name: "Player" }));
    await waitFor(() =>
      expect(screen.getByRole("combobox", { name: /role for bob/i })).toHaveTextContent("Player"),
    );

    await userEvent.click(screen.getByRole("combobox", { name: /role for carol/i }));
    await userEvent.click(await screen.findByRole("option", { name: "Admin" }));

    await waitFor(() =>
      expect(screen.getByRole("combobox", { name: /role for bob/i })).toHaveTextContent("Admin"),
    );
  });
});
