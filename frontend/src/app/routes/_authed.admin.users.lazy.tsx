import { createLazyFileRoute } from "@tanstack/react-router";
import { UsersPage } from "@/pages/admin/users";

export const Route = createLazyFileRoute("/_authed/admin/users")({ component: UsersPage });
