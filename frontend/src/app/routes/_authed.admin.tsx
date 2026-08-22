import { createFileRoute, redirect } from "@tanstack/react-router";
import { meQueryOptions } from "@/entities/game";

export const Route = createFileRoute("/_authed/admin")({
  // The parent `_authed` route has already ensured `me` (and redirected to
  // /login on 401), so this read is a cache hit and cannot 401 here. Its
  // only job is the role check — §9's "guarded on role === 'admin'".
  //
  // Division of responsibility with the bundle check
  // (`scripts/assert-admin-split.mjs`): the router plugin's
  // `autoCodeSplitting` splits a route's `component` into its own chunk
  // no matter which file declares it, so a `component` can never leak
  // into the player bundle from here — that half is the plugin's job, not
  // the script's. What the plugin cannot save you from is non-component
  // code in *this* eager file — `beforeLoad`, a `loader`, or anything
  // they import — which ships to every player unconditionally. Catching
  // that leak is exactly what `assert-admin-split.mjs` exists for.
  beforeLoad: async ({ context }) => {
    const me = await context.queryClient.ensureQueryData(meQueryOptions());
    if (me.role !== "admin") {
      // Home, not /login: signing in again would not help, and a login
      // form shown to someone already signed in is a dead end.
      throw redirect({ to: "/" });
    }
  },
});
