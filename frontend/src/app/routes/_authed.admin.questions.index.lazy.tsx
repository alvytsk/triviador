import { createLazyFileRoute } from "@tanstack/react-router";
import { QuestionsPage } from "@/pages/admin/questions";

export const Route = createLazyFileRoute("/_authed/admin/questions/")({ component: QuestionsPage });
