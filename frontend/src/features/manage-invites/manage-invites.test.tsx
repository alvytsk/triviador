import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { HttpResponse, http } from "msw";
import { describe, expect, it } from "vitest";
import type { InviteView } from "@/shared/api/generated/admin";
import { server } from "../../../testing/msw";
import { renderWithApp } from "../../../testing/render";
import { InviteTable } from "./ui/invite-table";
import { IssueDialog } from "./ui/issue-dialog";

function invite(overrides: Partial<InviteView> = {}): InviteView {
  return {
    id: "inv1",
    status: "pending",
    expires_at: "2026-08-28T00:00:00Z",
    used_by: null,
    ...overrides,
  };
}

describe("IssueDialog", () => {
  it("shows issued codes once, and says so", async () => {
    // The backend stores only a digest; the plaintext exists in exactly one
    // response. The dialog must present the codes copyably AND state they
    // will not be shown again — an admin who closes it without copying has
    // to issue new ones.
    server.use(
      http.post("/api/admin/invites", () =>
        HttpResponse.json(
          [
            { id: "inv1", code: "ABCD-1234", expires_at: "2026-08-28T00:00:00Z" },
            { id: "inv2", code: "EFGH-5678", expires_at: "2026-08-28T00:00:00Z" },
          ],
          { status: 201 },
        ),
      ),
    );
    renderWithApp(<IssueDialog />);

    await userEvent.click(screen.getByRole("button", { name: /issue invites/i }));
    await userEvent.click(screen.getByRole("button", { name: /^issue$/i }));

    expect(await screen.findByText("ABCD-1234")).toBeInTheDocument();
    expect(screen.getByText("EFGH-5678")).toBeInTheDocument();
    expect(screen.getByText(/will not be shown again/i)).toBeInTheDocument();
  });

  it("sends the entered count and expiry", async () => {
    let seenBody: unknown = null;
    server.use(
      http.post("/api/admin/invites", async ({ request }) => {
        seenBody = await request.json();
        return HttpResponse.json(
          [{ id: "inv1", code: "ABCD-1234", expires_at: "2026-08-28T00:00:00Z" }],
          { status: 201 },
        );
      }),
    );
    renderWithApp(<IssueDialog />);

    await userEvent.click(screen.getByRole("button", { name: /issue invites/i }));
    const countInput = screen.getByLabelText(/count/i);
    await userEvent.clear(countInput);
    await userEvent.type(countInput, "25");
    const expiryInput = screen.getByLabelText(/expires in hours/i);
    await userEvent.clear(expiryInput);
    await userEvent.type(expiryInput, "48");
    await userEvent.click(screen.getByRole("button", { name: /^issue$/i }));

    await waitFor(() => expect(seenBody).toEqual({ count: 25, expires_in_hours: 48 }));
  });
});

describe("InviteTable", () => {
  it("never asks for a code in the listing", async () => {
    // the list renders status only. If a code could be re-read from a list
    // endpoint, hashing it would be decorative.
    renderWithApp(<InviteTable invites={[invite()]} />);

    expect(screen.queryByLabelText(/code/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/^[A-Z0-9]{4}-[A-Z0-9]{4}$/)).not.toBeInTheDocument();
  });

  it("renders all four statuses", async () => {
    renderWithApp(
      <InviteTable
        invites={[
          invite({ id: "1", status: "pending" }),
          invite({ id: "2", status: "used", used_by: "alice" }),
          invite({ id: "3", status: "revoked" }),
          invite({ id: "4", status: "expired" }),
        ]}
      />,
    );

    expect(screen.getByText("Pending")).toBeInTheDocument();
    expect(screen.getByText("Used")).toBeInTheDocument();
    expect(screen.getByText("Revoked")).toBeInTheDocument();
    expect(screen.getByText("Expired")).toBeInTheDocument();
    expect(screen.getByText("alice")).toBeInTheDocument();
  });

  it("treats a second revoke as success, not an error", async () => {
    // the backend answers 200 both times: an admin clicking twice has not
    // made a mistake, and the second click is indistinguishable from a retry.
    let calls = 0;
    server.use(
      http.post("/api/admin/invites/inv1/revoke", () => {
        calls += 1;
        return HttpResponse.json(invite({ status: "revoked" }));
      }),
    );
    renderWithApp(<InviteTable invites={[invite()]} />);

    const revokeButton = screen.getByRole("button", { name: /revoke/i });
    await userEvent.click(revokeButton);
    await waitFor(() => expect(calls).toBe(1));
    expect(screen.queryByRole("status")).not.toBeInTheDocument();

    await userEvent.click(revokeButton);
    await waitFor(() => expect(calls).toBe(2));
    expect(screen.queryByRole("status")).not.toBeInTheDocument();
  });
});
