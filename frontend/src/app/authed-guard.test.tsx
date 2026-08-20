import { QueryClient } from "@tanstack/react-query";
import { isRedirect } from "@tanstack/react-router";
import { HttpResponse, http } from "msw";
import { describe, expect, it } from "vitest";
import { ApiFetchError } from "@/shared/api";
import { server } from "../../testing/msw";
import { Route } from "./routes/_authed";

/**
 * A deferred finding from Task 4: `ApiFetchError.isUnauthenticated` is true
 * only for the `unauthenticated` code, deliberately not for
 * `credentials_invalid` — a guard that redirected on a wrong password would
 * trap the user in a loop between the login form and the guard. `_authed`'s
 * guard is the first real consumer of that distinction, so it is asserted
 * here directly, against the guard's own `beforeLoad`.
 */
function envelope(code: string, status: number) {
  return http.get("/api/auth/me", () =>
    HttpResponse.json({ code, message: "nope", details: null }, { status }),
  );
}

describe("_authed guard", () => {
  it("redirects to /login when the session is unauthenticated", async () => {
    server.use(envelope("unauthenticated", 401));
    const queryClient = new QueryClient();

    let caught: unknown;
    try {
      await Route.options.beforeLoad?.({
        context: { queryClient },
        location: { href: "/games/g1" },
      } as never);
    } catch (error) {
      caught = error;
    }

    expect(isRedirect(caught)).toBe(true);
    expect((caught as { options: { to: string } }).options.to).toBe("/login");
  });

  it("does not redirect when the failure is credentials_invalid", async () => {
    server.use(envelope("credentials_invalid", 401));
    const queryClient = new QueryClient();

    let caught: unknown;
    try {
      await Route.options.beforeLoad?.({
        context: { queryClient },
        location: { href: "/games/g1" },
      } as never);
    } catch (error) {
      caught = error;
    }

    expect(isRedirect(caught)).toBe(false);
    expect(caught).toBeInstanceOf(ApiFetchError);
  });
});
