import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { HttpResponse, http } from "msw";
import { describe, expect, it } from "vitest";
import { server } from "../../../../testing/msw";
import { renderRoute } from "../../../../testing/render";

const ME_ADMIN = { user_id: "u1", username: "admin", display_name: "Admin", role: "admin" };

const CATEGORIES = [{ id: "cat1", name: "Geography", slug: "geography" }];

function withMe() {
  server.use(http.get("/api/auth/me", () => HttpResponse.json(ME_ADMIN)));
}

function withCategories() {
  server.use(http.get("/api/admin/categories", () => HttpResponse.json(CATEGORIES)));
}

function questionDetail(overrides: Record<string, unknown> = {}) {
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

async function fillCreateForm(prompt: string) {
  await screen.findByRole("combobox", { name: "Category" });
  await userEvent.type(screen.getByLabelText("Prompt"), prompt);
  for (const input of screen.getAllByLabelText(/^Choice \d$/)) {
    await userEvent.type(input, "An option");
  }
}

describe("QuestionEditorPage", () => {
  it("loads an existing question into the form", async () => {
    withMe();
    withCategories();
    server.use(http.get("/api/admin/questions/q1", () => HttpResponse.json(questionDetail())));

    renderRoute("/admin/questions/q1");

    expect(
      await screen.findByDisplayValue("Which river flows through Prague?", {}, { timeout: 3000 }),
    ).toBeInTheDocument();
  });

  it("creates a question and navigates to its id so a refresh cannot recreate it", async () => {
    withMe();
    withCategories();
    server.use(
      http.post("/api/admin/questions", () =>
        HttpResponse.json(
          { question: questionDetail({ id: "q-new" }), duplicate_of: [] },
          { status: 201 },
        ),
      ),
    );

    const { router } = renderRoute("/admin/questions/new");

    await fillCreateForm("A brand new prompt");
    await userEvent.click(screen.getByRole("button", { name: /create question/i }));

    await waitFor(() => expect(router.state.location.pathname).toBe("/admin/questions/q-new"));
  });

  it("carries the duplicate warning through the create → redirect, so it is still visible at the canonical id", async () => {
    withMe();
    withCategories();
    server.use(
      http.post("/api/admin/questions", () =>
        HttpResponse.json(
          { question: questionDetail({ id: "q-dup" }), duplicate_of: ["q9"] },
          { status: 201 },
        ),
      ),
      http.get("/api/admin/questions/q-dup", () =>
        HttpResponse.json(questionDetail({ id: "q-dup" })),
      ),
    );

    const { router } = renderRoute("/admin/questions/new");

    await fillCreateForm("A prompt that already exists");
    await userEvent.click(screen.getByRole("button", { name: /create question/i }));

    await waitFor(() => expect(router.state.location.pathname).toBe("/admin/questions/q-dup"));
    expect(await screen.findByText(/1 existing question/i)).toBeInTheDocument();
  });

  describe("inline category creation", () => {
    // Unlike `question-form.test.tsx`'s own "inline category creation"
    // tests (which prove the FORM half — the field gets set to the
    // created id), this is the only place that can prove the picker
    // shows the new category BY NAME: this page owns the real
    // `adminCategoriesQueryOptions()` query, so `useCreateCategory`'s
    // `invalidateQueries` genuinely triggers a refetch here, which a
    // stateful MSW category list (not a fixed fixture) can answer with
    // the row that now exists.
    it("shows the created category by name in the picker, then saves a question with it", async () => {
      withMe();
      const categories = [...CATEGORIES];
      server.use(
        http.get("/api/admin/categories", () => HttpResponse.json(categories)),
        http.post("/api/admin/categories", async ({ request }) => {
          const body = (await request.json()) as { name: string; slug: string };
          const created = { id: "cat-new", name: body.name, slug: body.slug };
          categories.push(created);
          return HttpResponse.json(created, { status: 201 });
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

      const { router } = renderRoute("/admin/questions/new");
      await screen.findByRole("combobox", { name: "Category" });

      await userEvent.click(screen.getByRole("button", { name: /new category/i }));
      await userEvent.type(screen.getByLabelText("Category name"), "Sports");
      await userEvent.type(screen.getByLabelText("Slug"), "sports");
      await userEvent.click(screen.getByRole("button", { name: /create category/i }));

      // The real query invalidation + refetch round trip: once it
      // resolves, the picker shows the new category by its NAME, not
      // just its opaque id — proof the picker itself, not just the
      // form's internal state, reflects the created row.
      await waitFor(() =>
        expect(screen.getByRole("combobox", { name: "Category" })).toHaveTextContent("Sports"),
      );

      await userEvent.type(screen.getByLabelText("Prompt"), "Which sport uses a shuttlecock?");
      for (const input of screen.getAllByLabelText(/^Choice \d$/)) {
        await userEvent.type(input, "An option");
      }
      await userEvent.click(screen.getByRole("button", { name: /create question/i }));

      await waitFor(() => expect(router.state.location.pathname).toBe("/admin/questions/q-new"));
    });

    it("shows the fixed sentence and keeps the form open on a slug_taken refusal", async () => {
      withMe();
      withCategories();
      server.use(
        http.post("/api/admin/categories", () =>
          HttpResponse.json(
            { code: "slug_taken", message: "a category with slug geography exists", details: null },
            { status: 409 },
          ),
        ),
      );

      renderRoute("/admin/questions/new");
      await screen.findByRole("combobox", { name: "Category" });

      await userEvent.click(screen.getByRole("button", { name: /new category/i }));
      await userEvent.type(screen.getByLabelText("Category name"), "Geography again");
      await userEvent.type(screen.getByLabelText("Slug"), "geography");
      await userEvent.click(screen.getByRole("button", { name: /create category/i }));

      expect(
        await screen.findByText(/a category with that slug already exists/i),
      ).toBeInTheDocument();
      // Refused, not silently discarded: the inline form is still open,
      // and the original category is still the only option.
      expect(screen.getByLabelText("Category name")).toHaveValue("Geography again");
      expect(screen.getByRole("combobox", { name: "Category" })).toHaveTextContent("Geography");
    });
  });
});
