import { useQuery } from "@tanstack/react-query";
import { useNavigate, useSearch } from "@tanstack/react-router";
import { adminQuestionsQueryOptions } from "@/entities/admin";
import {
  Banner,
  Button,
  Chip,
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/shared/ui";
import { hasActiveFilters, toAdminQuestionSearch } from "../model/types";
import { QuestionFilterBar } from "./question-filter-bar";

const KIND_LABEL: Record<string, string> = {
  multiple_choice: "Multiple choice",
  numeric: "Numeric",
};

const DIFFICULTY_LABEL: Record<string, string> = {
  easy: "Easy",
  medium: "Medium",
  hard: "Hard",
};

/**
 * §10.2's question list: server-side paged and filtered entirely through
 * the URL (`useSearch`/`useNavigate` against the registered route, rather
 * than an import of `_authed.admin.questions.index.tsx` — see
 * `pages/admin/questions/model/types.ts` for why pages can't reach up into
 * `app`). `keepPreviousData` (Task 2's `adminQuestionsQueryOptions`) keeps
 * the previous page's rows on screen while a filter or page change is in
 * flight, rather than a spinner on every keystroke.
 */
export function QuestionsPage() {
  // Route id, not path: Task 4 renamed the file to `...questions.index.tsx`
  // (see that file's own comment for why), which changes the *id*
  // `useSearch`'s `from` must match — the public path `/admin/questions`
  // is unaffected and is what `useNavigate` below still uses.
  const search = useSearch({ from: "/_authed/admin/questions/" });
  // Trailing slash — see question-filter-bar.tsx's comment on the same
  // line: Task 4 renamed the route file to `...questions.index.tsx`.
  const navigate = useNavigate({ from: "/admin/questions/" });
  const questions = useQuery(adminQuestionsQueryOptions(toAdminQuestionSearch(search)));

  const page = questions.data;
  const filtered = hasActiveFilters(search);

  function goToOffset(offset: number) {
    navigate({ to: "/admin/questions", search: (prev) => ({ ...prev, offset }) });
  }

  function clearFilters() {
    navigate({ to: "/admin/questions", search: (prev) => ({ limit: prev.limit, offset: 0 }) });
  }

  return (
    <div className="flex flex-col gap-6">
      <div className="flex items-center justify-between gap-4">
        <h1 className="font-display text-3xl tracking-wider text-gold">Questions</h1>
        {/* "Import", not "Import questions" — `admin-guard.test.tsx`
         *  (Task 1) locates `AdminShell`'s own nav link by `/questions/i`;
         *  a second link whose name also matches that regex would make
         *  that assertion ambiguous the moment this page renders for real. */}
        <a
          href="/admin/questions/import"
          className="text-[11px] font-semibold uppercase tracking-[0.14em] text-ink-dim hover:text-ink"
        >
          Import
        </a>
      </div>

      <QuestionFilterBar
        search={search}
        hideClear={page !== undefined && page.total === 0 && filtered}
      />

      {questions.isError ? (
        <Banner tone="bad">Could not load the question bank. Try again.</Banner>
      ) : questions.isPending ? (
        <p className="text-[13px] text-ink-dim">Loading…</p>
      ) : page !== undefined && page.total === 0 ? (
        filtered ? (
          <div className="flex flex-col items-start gap-3 border-2 border-line bg-panel px-6 py-8">
            <p className="text-[14px] text-ink">No questions match these filters.</p>
            <Button variant="ghost" onClick={clearFilters}>
              Clear filters
            </Button>
          </div>
        ) : (
          <div className="flex flex-col items-start gap-3 border-2 border-line bg-panel px-6 py-8">
            <p className="text-[14px] text-ink">
              No questions yet — import a starter set to get going.
            </p>
            {/* A styled `<a>`, not `<Button><a>` — nesting a `<button>`
             *  inside an `<a>` is invalid HTML (two interactive elements),
             *  and `Button` always renders a real `<button>`. */}
            <a
              href="/admin/questions/import"
              className="font-display text-xl tracking-wider px-6 h-12 inline-flex items-center justify-center bg-gold text-base hover:bg-gold-bright"
            >
              Get started
            </a>
          </div>
        )
      ) : (
        <>
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Prompt</TableHead>
                <TableHead>Kind</TableHead>
                <TableHead>Category</TableHead>
                <TableHead>Difficulty</TableHead>
                <TableHead>Status</TableHead>
                <TableHead>Media</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {(page?.items ?? []).map((question) => (
                <TableRow key={question.id}>
                  <TableCell>
                    {/* `<a href>`, not a typed `<Link to>` — `/admin/questions/$id`
                     *  is not in the generated route tree yet (Task 4 adds it),
                     *  and a `to` outside that tree fails `tsc --noEmit` outright.
                     *  Same call `admin-shell.tsx` made for its forward references. */}
                    <a
                      href={`/admin/questions/${question.id}`}
                      className="font-medium text-ink hover:text-gold"
                    >
                      {question.prompt}
                    </a>
                  </TableCell>
                  <TableCell>{KIND_LABEL[question.kind] ?? question.kind}</TableCell>
                  <TableCell>{question.category_slug}</TableCell>
                  <TableCell>
                    {DIFFICULTY_LABEL[question.difficulty] ?? question.difficulty}
                  </TableCell>
                  <TableCell>
                    <Chip className={question.is_active ? "" : "bg-track text-ink-faint"}>
                      {question.is_active ? "Active" : "Inactive"}
                    </Chip>
                  </TableCell>
                  <TableCell>{question.has_media ? "Yes" : "—"}</TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>

          {page !== undefined && (
            <div className="flex items-center justify-between gap-4">
              <p className="text-[11px] uppercase tracking-[0.14em] text-ink-dim">
                Showing {page.offset + 1}–{Math.min(page.offset + page.limit, page.total)} of{" "}
                {page.total}
              </p>
              <div className="flex items-center gap-2">
                <Button
                  variant="ghost"
                  disabled={page.offset === 0}
                  onClick={() => goToOffset(Math.max(0, page.offset - page.limit))}
                >
                  Previous
                </Button>
                <Button
                  variant="ghost"
                  disabled={page.offset + page.limit >= page.total}
                  onClick={() => goToOffset(page.offset + page.limit)}
                >
                  Next
                </Button>
              </div>
            </div>
          )}
        </>
      )}
    </div>
  );
}
