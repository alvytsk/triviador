import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { HttpResponse, http } from "msw";
import { describe, expect, it, vi } from "vitest";
import { server } from "../../../../testing/msw";
import { renderWithApp } from "../../../../testing/render";
import { RedeemForm } from "./redeem-form";

const ME = { user_id: "u2", username: "petra.k", display_name: "Petra", role: "player" };

async function fill(overrides: Partial<Record<string, string>> = {}) {
  await userEvent.type(screen.getByLabelText("INVITE CODE"), overrides.code ?? "7QK4M2XD9RTP");
  await userEvent.type(screen.getByLabelText("USERNAME"), overrides.username ?? "petra.k");
  await userEvent.type(screen.getByLabelText("DISPLAY NAME"), overrides.display ?? "Petra");
  await userEvent.type(screen.getByLabelText("PASSWORD"), overrides.password ?? "longenough1");
}

describe("RedeemForm", () => {
  it("sends exactly the four fields the contract declares", async () => {
    let body: Record<string, unknown> = {};
    server.use(
      http.post("/api/auth/redeem", async ({ request }) => {
        body = (await request.json()) as Record<string, unknown>;
        return HttpResponse.json(ME, { status: 201 });
      }),
    );
    const onDone = vi.fn();
    renderWithApp(<RedeemForm onDone={onDone} />);
    await fill();
    await userEvent.click(screen.getByRole("button", { name: /create account/i }));
    await waitFor(() => expect(onDone).toHaveBeenCalled());
    expect(Object.keys(body).sort()).toEqual(["code", "display_name", "password", "username"]);
  });

  it("refuses a short password before it reaches the network", async () => {
    const calls = vi.fn();
    server.use(
      http.post("/api/auth/redeem", () => {
        calls();
        return HttpResponse.json(ME, { status: 201 });
      }),
    );
    renderWithApp(<RedeemForm onDone={vi.fn()} />);
    await fill({ password: "short" });
    await userEvent.click(screen.getByRole("button", { name: /create account/i }));
    expect(await screen.findByText(/at least 8/i)).toBeInTheDocument();
    expect(calls).not.toHaveBeenCalled();
  });

  it("refuses a username the contract's pattern rejects", async () => {
    renderWithApp(<RedeemForm onDone={vi.fn()} />);
    await fill({ username: "petra k!" });
    await userEvent.click(screen.getByRole("button", { name: /create account/i }));
    expect(await screen.findByText(/letters, digits/i)).toBeInTheDocument();
  });

  it("surfaces invite_invalid from the server", async () => {
    server.use(
      http.post("/api/auth/redeem", () =>
        HttpResponse.json(
          { code: "invite_invalid", message: "invite code is not usable", details: null },
          { status: 401 },
        ),
      ),
    );
    renderWithApp(<RedeemForm onDone={vi.fn()} />);
    await fill();
    await userEvent.click(screen.getByRole("button", { name: /create account/i }));
    expect(await screen.findByRole("status")).toHaveTextContent("invite code is not usable");
  });
});
