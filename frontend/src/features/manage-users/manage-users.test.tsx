import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { HttpResponse, http } from "msw";
import { describe, expect, it } from "vitest";
import type { UserView } from "@/shared/api/generated/admin";
import { server } from "../../../testing/msw";
import { renderWithApp } from "../../../testing/render";
import { UserTable } from "./ui/user-table";

function user(overrides: Partial<UserView> = {}): UserView {
  return {
    id: "u1",
    username: "alexey",
    display_name: "Alexey",
    role: "admin",
    is_active: true,
    ...overrides,
  };
}

describe("UserTable", () => {
  it("renders username, display name, role and active state", async () => {
    renderWithApp(<UserTable users={[user()]} />);

    expect(screen.getByText("alexey")).toBeInTheDocument();
    expect(screen.getByText("Alexey")).toBeInTheDocument();
    expect(screen.getByText("Active")).toBeInTheDocument();
  });

  it("renders self_target's sentence when an admin deactivates themselves", async () => {
    // 409 self_target. A generic "something went wrong" here is the failure
    // this screen exists to avoid — the admin needs to know it was *their
    // own row*, and that another administrator has to do it.
    server.use(
      http.post("/api/admin/users/u1/deactivate", () =>
        HttpResponse.json(
          { code: "self_target", message: "you cannot deactivate your own account", details: null },
          { status: 409 },
        ),
      ),
    );
    renderWithApp(<UserTable users={[user()]} />);

    await userEvent.click(screen.getByRole("button", { name: /deactivate/i }));
    await userEvent.click(screen.getByRole("button", { name: /confirm deactivate/i }));

    expect(await screen.findByText(/you cannot do that to your own account/i)).toBeInTheDocument();
  });

  it("renders last_admin's sentence when the last admin is demoted", async () => {
    // 409 last_admin -> "promote someone else first".
    server.use(
      http.post("/api/admin/users/u1/role", () =>
        HttpResponse.json(
          { code: "last_admin", message: "this is the last administrator", details: null },
          { status: 409 },
        ),
      ),
    );
    renderWithApp(<UserTable users={[user()]} />);

    await userEvent.click(screen.getByRole("combobox", { name: /role for alexey/i }));
    await userEvent.click(await screen.findByRole("option", { name: "Player" }));

    expect(await screen.findByText(/promote someone else first/i)).toBeInTheDocument();
  });

  it("says deactivation signs the user out everywhere", async () => {
    // §10.5: deactivation kills sessions immediately and closes their socket.
    // The confirmation copy must say so — an admin who thinks this is a soft
    // flag will use it on a live player mid-game.
    renderWithApp(<UserTable users={[user()]} />);

    await userEvent.click(screen.getByRole("button", { name: /deactivate/i }));

    expect(screen.getByText(/sign(s|ed)? .*out/i)).toBeInTheDocument();
  });

  it("shows the new role after a successful change", async () => {
    server.use(
      http.post("/api/admin/users/u1/role", () => HttpResponse.json(user({ role: "player" }))),
      http.get("/api/admin/users", () => HttpResponse.json([user({ role: "player" })])),
    );
    renderWithApp(<UserTable users={[user()]} />);

    await userEvent.click(screen.getByRole("combobox", { name: /role for alexey/i }));
    await userEvent.click(await screen.findByRole("option", { name: "Player" }));

    await waitFor(() =>
      expect(screen.getByRole("combobox", { name: /role for alexey/i })).toHaveTextContent(
        "Player",
      ),
    );
  });

  it("shows an inactive user's state and offers no deactivate control", async () => {
    renderWithApp(<UserTable users={[user({ is_active: false })]} />);

    expect(screen.getByText("Inactive")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /deactivate/i })).not.toBeInTheDocument();
  });
});
