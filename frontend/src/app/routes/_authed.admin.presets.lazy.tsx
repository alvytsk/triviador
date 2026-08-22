import { createLazyFileRoute } from "@tanstack/react-router";
import { PresetsPage } from "@/pages/admin/presets";

export const Route = createLazyFileRoute("/_authed/admin/presets")({ component: PresetsPage });
