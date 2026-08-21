import { createFileRoute, redirect } from "@tanstack/react-router";
import { meQueryOptions } from "@/entities/game";

export const Route = createFileRoute("/_authed/admin")({
  // The parent `_authed` route has already ensured `me` (and redirected to
  // /login on 401), so this read is a cache hit and cannot 401 here. Its
  // only job is the role check — §9's "guarded on role === 'admin'".
  beforeLoad: async ({ context }) => {
    const me = await context.queryClient.ensureQueryData(meQueryOptions());
    if (me.role !== "admin") {
      // Home, not /login: signing in again would not help, and a login
      // form shown to someone already signed in is a dead end.
      throw redirect({ to: "/" });
    }
  },
});
