import { fireEvent, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { HttpResponse, http } from "msw";
import { describe, expect, it, vi } from "vitest";
import { server } from "../../../testing/msw";
import { renderWithApp } from "../../../testing/render";
import { ImportWizard } from "./ui/import-wizard";

function summary(overrides: Record<string, unknown> = {}) {
  return {
    import_id: "imp1",
    upload_sha256: "a".repeat(64),
    filename: "questions.csv",
    staged_key: "imp1/questions.csv",
    row_count: 3,
    rejected_count: 0,
    rejections: [],
    notices: [],
    status: "validated",
    confirmable: true,
    expires_at: "2026-08-22T00:00:00Z",
    ...overrides,
  };
}

function mockDryRun(response: Record<string, unknown>) {
  server.use(
    http.post("/api/admin/questions/import/dry-run", () =>
      HttpResponse.json(response, { status: 201 }),
    ),
  );
}

function csvFile() {
  return new File(["prompt,answer\nQ1,A1\n"], "questions.csv", { type: "text/csv" });
}

/**
 * `fireEvent.change`, not `userEvent.upload`: `question-form.test.tsx`
 * already found (and documented) that this jsdom environment's
 * pointer-event simulation silently no-ops `userEvent.upload` on a file
 * input — the input's own `onChange` never fires, no error thrown.
 */
function upload() {
  fireEvent.change(screen.getByLabelText(/upload questions file/i), {
    target: { files: [csvFile()] },
  });
}

describe("ImportWizard", () => {
  it("reads `confirmable` from the response rather than recomputing it", async () => {
    // rejected_count is 0 but confirmable is false — an expired upload,
    // where the server has folded status and expiry into the flag. A
    // screen that recomputed `rejected_count === 0` would show a live
    // CONFIRM button here; this is the only test that would catch it.
    mockDryRun(summary({ rejected_count: 0, confirmable: false, status: "validated" }));
    renderWithApp(<ImportWizard importId={undefined} onImportIdChange={vi.fn()} />);

    upload();

    const confirmButton = await screen.findByRole("button", { name: /confirm import/i });
    expect(confirmButton).toBeDisabled();
  });

  it("renders rejections by line number with their reason", async () => {
    mockDryRun(
      summary({
        rejected_count: 1,
        confirmable: false,
        rejections: [{ line: 4, reason: "unknown category slug" }],
      }),
    );
    renderWithApp(<ImportWizard importId={undefined} onImportIdChange={vi.fn()} />);

    upload();

    expect(await screen.findByText("4")).toBeInTheDocument();
    expect(screen.getByText("unknown category slug")).toBeInTheDocument();
  });

  it("shows notices as warnings that do not block confirm", async () => {
    // §10.2: a digest match is a warning on save and on import. If a
    // notice disabled CONFIRM, a file with one accidental repeat could
    // never be applied at all.
    mockDryRun(
      summary({
        rejected_count: 0,
        confirmable: true,
        notices: [{ line: 2, reason: "a question with this prompt is already in the bank" }],
      }),
    );
    renderWithApp(<ImportWizard importId={undefined} onImportIdChange={vi.fn()} />);

    upload();

    expect(
      await screen.findByText(/a question with this prompt is already in the bank/i),
    ).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /confirm import/i })).toBeEnabled();
  });

  it("downloads the rejected rows as CSV", async () => {
    mockDryRun(
      summary({
        rejected_count: 1,
        confirmable: false,
        rejections: [{ line: 2, reason: "bad" }],
      }),
    );
    let seenUrl: string | null = null;
    const csv = "line,prompt,reason\n2,What is 2+2?,bad\n";
    server.use(
      http.get("/api/admin/questions/import/imp1/rejected.csv", ({ request }) => {
        seenUrl = request.url;
        return HttpResponse.text(csv, { headers: { "content-type": "text/csv; charset=utf-8" } });
      }),
    );

    // An object holder, not a plain `let` reassigned only inside the
    // closure below — `imports.test.ts` (Task 2) already found the same
    // TypeScript control-flow quirk that narrows a bare `let`'s later use
    // to `never`.
    const seen: { blob: Blob | null } = { blob: null };
    vi.spyOn(URL, "createObjectURL").mockImplementation((blob) => {
      seen.blob = blob as Blob;
      return "blob:mock";
    });
    vi.spyOn(URL, "revokeObjectURL").mockImplementation(() => {});
    const clicked: HTMLAnchorElement[] = [];
    vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(function (
      this: HTMLAnchorElement,
    ) {
      clicked.push(this);
    });

    renderWithApp(<ImportWizard importId={undefined} onImportIdChange={vi.fn()} />);
    upload();

    await userEvent.click(await screen.findByRole("button", { name: /download rejected/i }));

    await waitFor(() => expect(seenUrl).not.toBeNull());
    expect(clicked).toHaveLength(1);
    expect(clicked[0]?.download).toBe("imp1-rejected.csv");
    await expect(seen.blob?.text()).resolves.toBe(csv);
  });

  it("surfaces import_not_confirmable on a second confirm", async () => {
    mockDryRun(summary({ rejected_count: 0, confirmable: true }));
    server.use(
      http.post("/api/admin/questions/import/imp1/confirm", () =>
        HttpResponse.json(
          { code: "import_not_confirmable", message: "this import expired", details: null },
          { status: 409 },
        ),
      ),
    );
    renderWithApp(<ImportWizard importId={undefined} onImportIdChange={vi.fn()} />);
    upload();

    await userEvent.click(await screen.findByRole("button", { name: /confirm import/i }));

    expect(
      await screen.findByText(/this upload can no longer be applied\. run the dry-run again\./i),
    ).toBeInTheDocument();
  });

  it("sends the file as raw bytes with X-Filename", async () => {
    const seen: { contentType: string | null; filename: string | null; body: string | null } = {
      contentType: null,
      filename: null,
      body: null,
    };
    server.use(
      http.post("/api/admin/questions/import/dry-run", async ({ request }) => {
        seen.contentType = request.headers.get("content-type");
        seen.filename = request.headers.get("x-filename");
        seen.body = await request.text();
        return HttpResponse.json(summary(), { status: 201 });
      }),
    );
    renderWithApp(<ImportWizard importId={undefined} onImportIdChange={vi.fn()} />);

    upload();

    await waitFor(() => expect(seen.filename).toBe("questions.csv"));
    expect(seen.contentType?.includes("multipart")).toBe(false);
    expect(seen.body).toBe("prompt,answer\nQ1,A1\n");
  });

  it("resumes an import by id after a reload, without the per-row report", async () => {
    // No GET exists for an import's report (only dry-run, rejected.csv and
    // confirm) — `importId` surviving in the URL is the only thing this
    // screen can recover after a hard reload.
    server.use(
      http.post("/api/admin/questions/import/imp9/confirm", () =>
        HttpResponse.json(summary({ import_id: "imp9", status: "confirmed" })),
      ),
    );
    const onImportIdChange = vi.fn();
    renderWithApp(<ImportWizard importId="imp9" onImportIdChange={onImportIdChange} />);

    expect(screen.queryByLabelText(/upload questions file/i)).not.toBeInTheDocument();
    const confirmButton = await screen.findByRole("button", { name: /confirm import/i });
    expect(confirmButton).toBeEnabled();

    await userEvent.click(confirmButton);

    await waitFor(() => expect(screen.getByText(/confirmed/i)).toBeInTheDocument());
  });
});
