import { createLazyFileRoute } from "@tanstack/react-router";
import { InvitesPage } from "@/pages/admin/invites";

export const Route = createLazyFileRoute("/_authed/admin/invites")({ component: InvitesPage });
