import { createLazyFileRoute } from "@tanstack/react-router";
import { AdminShell } from "@/pages/admin/shell";

export const Route = createLazyFileRoute("/_authed/admin")({ component: AdminShell });
