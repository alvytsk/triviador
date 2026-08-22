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

function invite(overrides: Record<string, unknown> = {}) {
  return {
    id: "inv1",
    status: "pending",
    expires_at: "2026-08-28T00:00:00Z",
    used_by: null,
    ...overrides,
  };
}

describe("InvitesPage", () => {
  it("loads and renders the invite list through the real route", async () => {
    withMe();
    server.use(http.get("/api/admin/invites", () => HttpResponse.json([invite()])));

    renderRoute("/admin/invites");

    expect(await screen.findByText("Pending")).toBeInTheDocument();
  });

  it("refetches the list after issuing, so new codes' rows show up without a manual reload", async () => {
    withMe();
    let listCalls = 0;
    server.use(
      http.get("/api/admin/invites", () => {
        listCalls += 1;
        return HttpResponse.json(listCalls === 1 ? [] : [invite({ id: "inv-new" })]);
      }),
      http.post("/api/admin/invites", () =>
        HttpResponse.json(
          [{ id: "inv-new", code: "WXYZ-0001", expires_at: "2026-08-28T00:00:00Z" }],
          { status: 201 },
        ),
      ),
    );

    renderRoute("/admin/invites");

    await screen.findByText(/no invites yet/i);
    await userEvent.click(screen.getByRole("button", { name: /issue invites/i }));
    await userEvent.click(screen.getByRole("button", { name: /^issue$/i }));

    expect(await screen.findByText("WXYZ-0001")).toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: /done/i }));
    await waitFor(() => expect(screen.getByText("Pending")).toBeInTheDocument());
  });
});
