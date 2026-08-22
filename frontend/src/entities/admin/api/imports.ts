import { ApiFetchError, apiFetch, apiSend, errorEnvelopeSchema } from "@/shared/api";
import { type ImportSummary, importSummarySchema } from "@/shared/api/generated/admin";

/**
 * `POST /api/admin/questions/import/dry-run` reads the raw stream, the
 * same raw-body decision `uploadMedia` documents. The filename cannot ride
 * in the body next to it, so it goes in `X-Filename` instead — the backend
 * uses it only to choose `.zip` vs `.csv` parsing and to name the staged
 * object, never as a path (`imports.py`).
 *
 * Bytes via `file.arrayBuffer()`, not the `File` object itself — see
 * `uploadMedia`'s comment on why a jsdom-constructed `File` cannot be
 * trusted as a fetch body in this test environment.
 */
export async function dryRunImport(file: File): Promise<ImportSummary> {
  const bytes = await file.arrayBuffer();
  return apiFetch("/api/admin/questions/import/dry-run", importSummarySchema, {
    method: "POST",
    body: bytes,
    headers: { "content-type": file.type, "x-filename": file.name },
  });
}

export function confirmImport(importId: string): Promise<ImportSummary> {
  return apiSend(`/api/admin/questions/import/${importId}/confirm`, importSummarySchema, undefined);
}

/**
 * `GET /api/admin/questions/import/{id}/rejected.csv` answers `text/csv`,
 * not a JSON envelope — `apiFetch` cannot be reused for the success path,
 * since it always tries `JSON.parse` on a non-empty body. The error path
 * still answers the usual envelope (`ApiError` on the backend), so a
 * failure is classified the same way `apiFetch` classifies one, just
 * inlined rather than imported: `rest.ts`'s envelope-vs-transport split is
 * intentionally not exported as a standalone helper (Task 2's brief scopes
 * this slice to `entities/admin/`, not to widening `shared/api`'s surface
 * for a single caller).
 */
export async function fetchRejectedCsv(importId: string): Promise<string> {
  const response = await fetch(`/api/admin/questions/import/${importId}/rejected.csv`, {
    credentials: "same-origin",
  });
  const text = await response.text();
  if (!response.ok) {
    let parsed: unknown;
    try {
      parsed = JSON.parse(text);
    } catch {
      throw new ApiFetchError({
        kind: "transport",
        message: `the server returned ${response.status} and a body that is not an error envelope`,
        status: response.status,
      });
    }
    const envelope = errorEnvelopeSchema.safeParse(parsed);
    if (!envelope.success) {
      throw new ApiFetchError({
        kind: "transport",
        message: `the server returned ${response.status} and a body that is not an error envelope`,
        status: response.status,
      });
    }
    throw new ApiFetchError({
      kind: "envelope",
      message: envelope.data.message,
      status: response.status,
      code: envelope.data.code,
      details: envelope.data.details,
    });
  }
  return text;
}
