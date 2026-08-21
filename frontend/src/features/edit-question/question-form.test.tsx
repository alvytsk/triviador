import { fireEvent, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { HttpResponse, http } from "msw";
import { describe, expect, it, vi } from "vitest";
import type { CategoryView, QuestionDetail } from "@/shared/api/generated/admin";
import { server } from "../../../testing/msw";
import { renderWithApp } from "../../../testing/render";
import { QuestionForm } from "./ui/question-form";

const CATEGORIES: CategoryView[] = [{ id: "cat1", name: "Geography", slug: "geography" }];

function questionDetail(overrides: Partial<QuestionDetail> = {}): QuestionDetail {
  return {
    id: "q1",
    kind: "multiple_choice",
    prompt: "Which river flows through Prague?",
    category_id: "cat1",
    category_slug: "geography",
    difficulty: "easy",
    is_active: true,
    version: 1,
    media_asset_id: null,
    choices: [
      { idx: 0, text: "Vltava", is_correct: true, media_asset_id: null },
      { idx: 1, text: "Labe", is_correct: false, media_asset_id: null },
      { idx: 2, text: "Morava", is_correct: false, media_asset_id: null },
      { idx: 3, text: "Odra", is_correct: false, media_asset_id: null },
    ],
    numeric_answer: null,
    unit: null,
    ...overrides,
  };
}

describe("QuestionForm", () => {
  it("keeps the prompt when the kind changes", async () => {
    renderWithApp(<QuestionForm mode="create" categories={CATEGORIES} onSaved={vi.fn()} />);

    await userEvent.type(screen.getByLabelText("Prompt"), "Which river flows through Prague?");
    await userEvent.click(screen.getByRole("combobox", { name: "Kind" }));
    await userEvent.click(await screen.findByRole("option", { name: "Numeric" }));

    expect(screen.getByLabelText("Prompt")).toHaveValue("Which river flows through Prague?");
  });

  it("fixes the choice count at four", () => {
    renderWithApp(<QuestionForm mode="create" categories={CATEGORIES} onSaved={vi.fn()} />);

    // Exactly four choice text inputs and four correctness radios — no
    // control adds a fifth or removes the fourth (§10.2).
    expect(screen.getAllByRole("radio")).toHaveLength(4);
    expect(screen.getAllByLabelText(/^Choice \d$/)).toHaveLength(4);
    expect(screen.queryByRole("button", { name: /add choice/i })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /remove choice/i })).not.toBeInTheDocument();
  });

  it("moves the correct marker rather than accumulating it", async () => {
    renderWithApp(<QuestionForm mode="create" categories={CATEGORIES} onSaved={vi.fn()} />);

    const radios = screen.getAllByRole("radio") as HTMLInputElement[];
    expect(radios[0]?.checked).toBe(true);

    await userEvent.click(radios[2] as HTMLInputElement);

    expect(radios[2]?.checked).toBe(true);
    expect(radios[0]?.checked).toBe(false);
    expect(radios.filter((r) => r.checked)).toHaveLength(1);
  });

  it("saves a duplicate prompt and warns", async () => {
    server.use(
      http.patch("/api/admin/questions/q1", () =>
        HttpResponse.json({ question: questionDetail(), duplicate_of: ["q9"] }, { status: 200 }),
      ),
    );
    renderWithApp(
      <QuestionForm
        mode="edit"
        question={questionDetail()}
        categories={CATEGORIES}
        onSaved={vi.fn()}
      />,
    );

    await userEvent.click(screen.getByRole("button", { name: /save changes/i }));

    expect(await screen.findByRole("status")).toHaveTextContent(/1 existing question/i);
  });

  it("keeps the form when an image is rejected, showing the server's own reason", async () => {
    // `media_rejected` has 6 distinct raise sites in `triviador/media/
    // pipeline.py`, each with its own message — this screen must show
    // whichever one the server actually sent, not a fixed sentence that
    // could only be right for one of them (see `shared/lib/admin-errors.ts`).
    server.use(
      http.post("/api/admin/media", () =>
        HttpResponse.json(
          {
            code: "media_rejected",
            message: "that file is not an image this server can decode",
            details: null,
          },
          { status: 415 },
        ),
      ),
    );
    renderWithApp(<QuestionForm mode="create" categories={CATEGORIES} onSaved={vi.fn()} />);

    await userEvent.type(screen.getByLabelText("Prompt"), "half typed prompt survives");
    const file = new File(["nope"], "x.txt", { type: "text/plain" });
    // `fireEvent.change`, not `userEvent.upload`: this jsdom environment's
    // pointer-event simulation silently no-ops `userEvent.upload` on a
    // file input (confirmed in isolation — the input's own `onChange`
    // never fires, no error thrown) — `fireEvent.change` is the same
    // native event a real file picker dispatches, without going through
    // that broken click sequence.
    fireEvent.change(screen.getByLabelText("Upload media"), { target: { files: [file] } });

    expect(
      await screen.findByText(/that file is not an image this server can decode/i),
    ).toBeInTheDocument();
    expect(screen.getByLabelText("Prompt")).toHaveValue("half typed prompt survives");
  });

  it("flips active state without navigating away", async () => {
    server.use(
      http.post("/api/admin/questions/q1/deactivate", () =>
        HttpResponse.json(questionDetail({ is_active: false })),
      ),
      http.post("/api/admin/questions/q1/activate", () =>
        HttpResponse.json(questionDetail({ is_active: true })),
      ),
    );
    renderWithApp(
      <QuestionForm
        mode="edit"
        question={questionDetail()}
        categories={CATEGORIES}
        onSaved={vi.fn()}
      />,
    );

    expect(screen.getByText("Active")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: /deactivate/i }));
    await waitFor(() => expect(screen.getByText("Inactive")).toBeInTheDocument());

    await userEvent.click(screen.getByRole("button", { name: /activate/i }));
    await waitFor(() => expect(screen.getByText("Active")).toBeInTheDocument());
  });

  describe("inline category creation", () => {
    // `QuestionForm` itself only ever renders whatever `categories` prop
    // it is given — it does not own the categories query, so this level
    // can only prove the FORM half of the feature: the field is set to
    // the created id, the inline form closes, and a save afterward
    // actually carries that id. Proving the picker shows the new
    // category *by name* needs the real query + invalidation round trip,
    // which only exists one level up — see
    // `question-editor-page.test.tsx`'s own "inline category creation"
    // test for that half.
    it("selects the created category by id and saves a question with it", async () => {
      // Empty categories — the fresh-install scenario the gap actually
      // matters for: on a brand-new server the picker has nothing to
      // offer until this affordance exists.
      server.use(
        http.post("/api/admin/categories", async ({ request }) => {
          const body = (await request.json()) as { name: string; slug: string };
          return HttpResponse.json(
            { id: "cat-new", name: body.name, slug: body.slug },
            { status: 201 },
          );
        }),
        http.post("/api/admin/questions", async ({ request }) => {
          const body = (await request.json()) as Record<string, unknown>;
          return HttpResponse.json(
            {
              question: questionDetail({
                id: "q-new",
                category_id: body.category_id as string,
                category_slug: "sports",
                prompt: body.prompt as string,
              }),
              duplicate_of: [],
            },
            { status: 201 },
          );
        }),
      );
      const onSaved = vi.fn();
      renderWithApp(<QuestionForm mode="create" categories={[]} onSaved={onSaved} />);

      // The picker starts genuinely empty — no category to choose yet.
      expect(screen.queryByRole("option")).not.toBeInTheDocument();

      await userEvent.click(screen.getByRole("button", { name: /new category/i }));
      await userEvent.type(screen.getByLabelText("Category name"), "Sports");
      await userEvent.type(screen.getByLabelText("Slug"), "sports");
      await userEvent.click(screen.getByRole("button", { name: /create category/i }));

      // The inline form closes once its job is done.
      await waitFor(() => expect(screen.queryByLabelText("Category name")).not.toBeInTheDocument());

      await userEvent.type(screen.getByLabelText("Prompt"), "Which sport uses a shuttlecock?");
      // The generated schema requires non-empty choice text — this test
      // actually submits, unlike the kind/correctness tests above, so
      // the four blank choices `blankChoices()` starts with need real
      // text or `questionWriteRequestSchema`'s own `onSubmit` validator
      // blocks the mutation before it ever fires.
      await userEvent.type(screen.getByLabelText("Choice 1"), "Badminton");
      await userEvent.type(screen.getByLabelText("Choice 2"), "Tennis");
      await userEvent.type(screen.getByLabelText("Choice 3"), "Squash");
      await userEvent.type(screen.getByLabelText("Choice 4"), "Table tennis");
      await userEvent.click(screen.getByRole("button", { name: /create question/i }));

      // The save carries the created category's id — proof the field was
      // actually selected, not just that creation succeeded somewhere.
      await waitFor(() => expect(onSaved).toHaveBeenCalledTimes(1));
      expect(onSaved.mock.calls[0]?.[0]).toMatchObject({ id: "q-new", category_id: "cat-new" });
    });

    it("shows the fixed sentence and keeps the form open on a slug_taken refusal", async () => {
      server.use(
        http.post("/api/admin/categories", () =>
          HttpResponse.json(
            { code: "slug_taken", message: "a category with slug geography exists", details: null },
            { status: 409 },
          ),
        ),
      );
      renderWithApp(<QuestionForm mode="create" categories={CATEGORIES} onSaved={vi.fn()} />);

      await userEvent.click(screen.getByRole("button", { name: /new category/i }));
      await userEvent.type(screen.getByLabelText("Category name"), "Geography again");
      await userEvent.type(screen.getByLabelText("Slug"), "geography");
      await userEvent.click(screen.getByRole("button", { name: /create category/i }));

      // `shared/lib/admin-errors.ts`'s fixed sentence, not the fake
      // backend's own message — proving the override list, not just that
      // *some* error rendered.
      expect(
        await screen.findByText(/a category with that slug already exists/i),
      ).toBeInTheDocument();
      // Refused, not silently discarded: the inline form is still open
      // with what the admin typed, and the original category list is
      // untouched.
      expect(screen.getByLabelText("Category name")).toHaveValue("Geography again");
      expect(screen.getByRole("combobox", { name: "Category" })).toHaveTextContent("Geography");
    });
  });
});
