import { HttpResponse, http } from "msw";
import { describe, expect, it } from "vitest";
import { ApiFetchError } from "@/shared/api";
import { server } from "../../../../testing/msw";
import { confirmImport, dryRunImport, fetchRejectedCsv } from "./imports";

const SUMMARY = {
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
};

describe("dryRunImport", () => {
  it("sends the file's bytes as the body with the filename in X-Filename, not FormData", async () => {
    // A plain `let` reassigned only inside this closure hits a TypeScript
    // control-flow quirk that narrows its later use to `never` — see
    // `media.ts`'s comment on jsdom's `File` for the same class of
    // test-environment-only gotcha. An object holder sidesteps it.
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
        return HttpResponse.json(SUMMARY, { status: 201 });
      }),
    );
    const file = new File(["prompt,answer\nQ1,A1\n"], "questions.csv", { type: "text/csv" });
    await expect(dryRunImport(file)).resolves.toEqual(SUMMARY);
    expect(seen.contentType?.includes("multipart")).toBe(false);
    expect(seen.filename).toBe("questions.csv");
    expect(seen.body).toBe("prompt,answer\nQ1,A1\n");
  });

  it("raises a transport ApiFetchError when the success body does not match the schema", async () => {
    server.use(
      http.post("/api/admin/questions/import/dry-run", () =>
        HttpResponse.json({ import_id: "imp1" }, { status: 201 }),
      ),
    );
    const file = new File(["x"], "q.csv", { type: "text/csv" });
    const error = await dryRunImport(file).catch((e: unknown) => e);
    expect(error).toBeInstanceOf(ApiFetchError);
    expect((error as ApiFetchError).kind).toBe("transport");
  });
});

describe("confirmImport", () => {
  it("posts to the import's confirm route and parses the resulting summary", async () => {
    server.use(
      http.post("/api/admin/questions/import/imp1/confirm", () =>
        HttpResponse.json({ ...SUMMARY, status: "confirmed" }),
      ),
    );
    await expect(confirmImport("imp1")).resolves.toEqual({ ...SUMMARY, status: "confirmed" });
  });

  it("surfaces import_not_confirmable as an envelope-kind ApiFetchError", async () => {
    server.use(
      http.post("/api/admin/questions/import/imp1/confirm", () =>
        HttpResponse.json(
          { code: "import_not_confirmable", message: "this import expired", details: null },
          { status: 409 },
        ),
      ),
    );
    const error = await confirmImport("imp1").catch((e: unknown) => e);
    expect(error).toBeInstanceOf(ApiFetchError);
    expect((error as ApiFetchError).code).toBe("import_not_confirmable");
  });
});

describe("fetchRejectedCsv", () => {
  it("returns the raw csv text, not parsed as JSON", async () => {
    const csv = "line,prompt,reason\n2,What is 2+2?,duplicate prompt\n";
    server.use(
      http.get("/api/admin/questions/import/imp1/rejected.csv", () =>
        HttpResponse.text(csv, { headers: { "content-type": "text/csv; charset=utf-8" } }),
      ),
    );
    await expect(fetchRejectedCsv("imp1")).resolves.toBe(csv);
  });

  it("throws when the import is not found", async () => {
    server.use(
      http.get("/api/admin/questions/import/imp1/rejected.csv", () =>
        HttpResponse.json(
          { code: "not_found", message: "no such import", details: null },
          { status: 404 },
        ),
      ),
    );
    await expect(fetchRejectedCsv("imp1")).rejects.toThrow();
  });
});
