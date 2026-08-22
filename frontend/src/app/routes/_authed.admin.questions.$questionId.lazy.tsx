import { createLazyFileRoute } from "@tanstack/react-router";
import { QuestionEditorPage } from "@/pages/admin/question-editor";

export const Route = createLazyFileRoute("/_authed/admin/questions/$questionId")({
  component: QuestionEditorPage,
});
