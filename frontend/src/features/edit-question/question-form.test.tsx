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
});
