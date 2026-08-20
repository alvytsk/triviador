import { createFileRoute, Outlet, redirect } from "@tanstack/react-router";
import { meQueryOptions } from "@/entities/game";
import { ApiFetchError } from "@/shared/api";
import { SocketStatusBanner } from "../socket-status";

export const Route = createFileRoute("/_authed")({
  beforeLoad: async ({ context, location }) => {
    try {
      await context.queryClient.ensureQueryData(meQueryOptions());
    } catch (error) {
      if (error instanceof ApiFetchError && error.isUnauthenticated) {
        throw redirect({ to: "/login", search: { next: location.href } });
      }
      throw error;
    }
  },
  component: () => (
    <>
      <SocketStatusBanner />
      <Outlet />
    </>
  ),
});
