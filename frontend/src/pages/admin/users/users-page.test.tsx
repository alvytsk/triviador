import { screen } from "@testing-library/react";
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
});
