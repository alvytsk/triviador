import { QueryClient } from "@tanstack/react-query";
import { HttpResponse, http } from "msw";
import { describe, expect, it } from "vitest";
import { ApiFetchError } from "@/shared/api";
import { server } from "../../../../testing/msw";
import { adminUsersQueryOptions, deactivateUser, setUserRole } from "./users";

const USER_VIEW = {
  id: "u1",
  username: "alexey",
  display_name: "Alexey",
  role: "player",
  is_active: true,
};

describe("adminUsersQueryOptions", () => {
  it("fetches and parses the user list", async () => {
    server.use(http.get("/api/admin/users", () => HttpResponse.json([USER_VIEW])));
    const client = new QueryClient();
    await expect(client.fetchQuery(adminUsersQueryOptions())).resolves.toEqual([USER_VIEW]);
  });
});

describe("deactivateUser", () => {
  it("posts to the deactivate route and parses the updated user", async () => {
    server.use(
      http.post("/api/admin/users/u1/deactivate", () =>
        HttpResponse.json({ ...USER_VIEW, is_active: false }),
      ),
    );
    await expect(deactivateUser("u1")).resolves.toEqual({ ...USER_VIEW, is_active: false });
  });

  it("surfaces self_target as an envelope-kind ApiFetchError", async () => {
    server.use(
      http.post("/api/admin/users/u1/deactivate", () =>
        HttpResponse.json(
          { code: "self_target", message: "you cannot deactivate your own account", details: null },
          { status: 409 },
        ),
      ),
    );
    const error = await deactivateUser("u1").catch((e: unknown) => e);
    expect(error).toBeInstanceOf(ApiFetchError);
    expect((error as ApiFetchError).code).toBe("self_target");
  });
});

describe("setUserRole", () => {
  it("posts the role and parses the updated user", async () => {
    let seenBody: unknown = null;
    server.use(
      http.post("/api/admin/users/u1/role", async ({ request }) => {
        seenBody = await request.json();
        return HttpResponse.json({ ...USER_VIEW, role: "admin" });
      }),
    );
    await expect(setUserRole("u1", { role: "admin" })).resolves.toEqual({
      ...USER_VIEW,
      role: "admin",
    });
    expect(seenBody).toEqual({ role: "admin" });
  });

  it("surfaces last_admin as an envelope-kind ApiFetchError", async () => {
    server.use(
      http.post("/api/admin/users/u1/role", () =>
        HttpResponse.json(
          { code: "last_admin", message: "this is the last administrator", details: null },
          { status: 409 },
        ),
      ),
    );
    const error = await setUserRole("u1", { role: "player" }).catch((e: unknown) => e);
    expect(error).toBeInstanceOf(ApiFetchError);
    expect((error as ApiFetchError).code).toBe("last_admin");
  });
});
