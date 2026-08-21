import { useQuery } from "@tanstack/react-query";
import { useNavigate, useParams } from "@tanstack/react-router";
import { adminCategoriesQueryOptions, adminQuestionQueryOptions } from "@/entities/admin";
import { QuestionForm } from "@/features/edit-question";
import type { QuestionDetail } from "@/shared/api/generated/admin";
import { Banner } from "@/shared/ui";

/**
 * `/admin/questions/new` is the creation form; any other id edits that
 * question (Task 4's Step 5). Both the category list and — when editing —
 * the question itself are fetched here rather than in the eager route
 * file: `_authed.admin.questions.$questionId.tsx`'s own comment explains
 * why a `loader` there would drag `entities/admin` (and every schema
 * `generated/admin.ts` constructs) into the player bundle.
 */
export function QuestionEditorPage() {
  const { questionId } = useParams({ from: "/_authed/admin/questions/$questionId" });
  const isNew = questionId === "new";
  const navigate = useNavigate({ from: "/admin/questions/$questionId" });

  const categories = useQuery(adminCategoriesQueryOptions());
  const question = useQuery({ ...adminQuestionQueryOptions(questionId), enabled: !isNew });

  function handleSaved(saved: QuestionDetail) {
    // On a successful create, move to the saved id so a refresh re-fetches
    // that question instead of submitting the same "new" form a second
    // time. An edit stays put — it is already at its canonical URL.
    if (isNew) {
      navigate({ to: "/admin/questions/$questionId", params: { questionId: saved.id } });
    }
  }

  if (categories.isError || (!isNew && question.isError)) {
    return <Banner tone="bad">Could not load this question. Try again.</Banner>;
  }
  if (categories.isPending || (!isNew && question.isPending)) {
    return <p className="text-[13px] text-ink-dim">Loading…</p>;
  }

  return (
    <div className="flex flex-col gap-6">
      <h1 className="font-display text-3xl tracking-wider text-gold">
        {isNew ? "New question" : "Edit question"}
      </h1>
      {isNew ? (
        <QuestionForm mode="create" categories={categories.data ?? []} onSaved={handleSaved} />
      ) : (
        <QuestionForm
          mode="edit"
          question={question.data as QuestionDetail}
          categories={categories.data ?? []}
          onSaved={handleSaved}
        />
      )}
    </div>
  );
}
