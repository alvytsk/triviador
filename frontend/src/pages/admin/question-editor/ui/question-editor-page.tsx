import { useQuery } from "@tanstack/react-query";
import { useNavigate, useParams, useSearch } from "@tanstack/react-router";
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
  const search = useSearch({ from: "/_authed/admin/questions/$questionId" });
  const isNew = questionId === "new";
  const navigate = useNavigate({ from: "/admin/questions/$questionId" });

  const categories = useQuery(adminCategoriesQueryOptions());
  const question = useQuery({ ...adminQuestionQueryOptions(questionId), enabled: !isNew });

  function handleSaved(saved: QuestionDetail, duplicateOf: string[]) {
    // On a successful create, move to the saved id so a refresh re-fetches
    // that question instead of submitting the same "new" form a second
    // time. An edit stays put — it is already at its canonical URL.
    //
    // `duplicateOf` rides along as a search param, not `QuestionForm`'s
    // own local state: this `navigate` unmounts the create-mode form
    // instance (and whatever `useQuestionForm` held for it) the moment it
    // fires, so anything held only in that instance's state is already
    // gone by the time the editor re-mounts at the new URL. The param is
    // what survives that remount — read below and rendered by this page,
    // independent of `QuestionForm`'s own duplicate-warning state, which
    // keeps serving the edit-in-place save exactly as before.
    //
    // `replace: true`: once the question exists at its own id, leaving
    // `/admin/questions/new` reachable via the back button would offer a
    // stale, already-submitted creation form.
    if (isNew) {
      navigate({
        to: "/admin/questions/$questionId",
        params: { questionId: saved.id },
        search: { duplicateOf: duplicateOf.length > 0 ? duplicateOf : undefined },
        replace: true,
      });
    }
  }

  if (categories.isError || (!isNew && question.isError)) {
    return <Banner tone="bad">Could not load this question. Try again.</Banner>;
  }
  if (categories.isPending || (!isNew && question.isPending)) {
    return <p className="text-[13px] text-ink-dim">Loading…</p>;
  }

  const duplicateOf = search.duplicateOf ?? [];

  return (
    <div className="flex flex-col gap-6">
      <h1 className="font-display text-3xl tracking-wider text-gold">
        {isNew ? "New question" : "Edit question"}
      </h1>

      {/* A warning carried through the create → redirect (see
       *  `handleSaved` above), never a blocking error — §10.2 says
       *  legitimately similar phrasings exist. */}
      {duplicateOf.length > 0 && (
        <Banner tone="warn">
          Saved — but this prompt closely matches {duplicateOf.length} existing question
          {duplicateOf.length === 1 ? "" : "s"} already in the bank.
        </Banner>
      )}

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
