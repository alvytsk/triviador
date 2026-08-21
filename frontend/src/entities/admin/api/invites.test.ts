import { QueryClient } from "@tanstack/react-query";
import { HttpResponse, http } from "msw";
import { describe, expect, it } from "vitest";
import { ApiFetchError } from "@/shared/api";
import { server } from "../../../../testing/msw";
import { adminInvitesQueryOptions, issueInvites, revokeInvite } from "./invites";

const INVITE_VIEW = {
  id: "inv1",
  status: "pending",
  expires_at: "2026-08-28T00:00:00Z",
  used_by: null,
};

describe("adminInvitesQueryOptions", () => {
  it("fetches and parses the invite list", async () => {
    server.use(http.get("/api/admin/invites", () => HttpResponse.json([INVITE_VIEW])));
    const client = new QueryClient();
    await expect(client.fetchQuery(adminInvitesQueryOptions())).resolves.toEqual([INVITE_VIEW]);
  });
});

describe("issueInvites", () => {
  it("posts the count and expiry, and parses the issued invites (which carry the code)", async () => {
    let seenBody: unknown = null;
    const issued = [{ id: "inv1", code: "ABCD-1234", expires_at: "2026-08-28T00:00:00Z" }];
    server.use(
      http.post("/api/admin/invites", async ({ request }) => {
        seenBody = await request.json();
        return HttpResponse.json(issued, { status: 201 });
      }),
    );
    await expect(issueInvites({ count: 1, expires_in_hours: 168 })).resolves.toEqual(issued);
    expect(seenBody).toEqual({ count: 1, expires_in_hours: 168 });
  });
});

describe("revokeInvite", () => {
  it("posts to the revoke route and parses the updated invite view", async () => {
    server.use(
      http.post("/api/admin/invites/inv1/revoke", () =>
        HttpResponse.json({ ...INVITE_VIEW, status: "revoked" }),
      ),
    );
    await expect(revokeInvite("inv1")).resolves.toEqual({ ...INVITE_VIEW, status: "revoked" });
  });

  it("raises a transport ApiFetchError when the response does not match the schema", async () => {
    server.use(
      http.post("/api/admin/invites/inv1/revoke", () => HttpResponse.json({ id: "inv1" })),
    );
    const error = await revokeInvite("inv1").catch((e: unknown) => e);
    expect(error).toBeInstanceOf(ApiFetchError);
    expect((error as ApiFetchError).kind).toBe("transport");
  });
});
