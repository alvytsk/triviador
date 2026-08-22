import { createLazyFileRoute } from "@tanstack/react-router";
import { ImportPage } from "@/pages/admin/import";

export const Route = createLazyFileRoute("/_authed/admin/questions/import")({
  component: ImportPage,
});
