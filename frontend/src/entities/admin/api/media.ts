import { apiFetch } from "@/shared/api";
import { type MediaAssetSummary, mediaAssetSummarySchema } from "@/shared/api/generated/admin";

/**
 * `POST /api/admin/media` reads the raw stream, not a multipart form
 * (Plan 7A's Task 3): the file *is* the body, with its own `Content-Type`.
 * A `FormData` body would send `multipart/form-data` and fail against the
 * real backend — the request-shape assertion in `media.test.ts` is what
 * keeps that true here.
 *
 * Sent as the file's raw bytes (`file.arrayBuffer()`), not the `File`
 * object itself: a browser's `fetch` accepts either identically, but a
 * `File` constructed by jsdom in the test environment is not the same
 * class Node's `fetch` recognizes as a streamable body, and is read back
 * as the four bytes of the string `"undefined"` instead of the file's
 * content — a test-environment mismatch, not a server behavior difference,
 * and `arrayBuffer()` sidesteps it in both places at once.
 */
export async function uploadMedia(file: File): Promise<MediaAssetSummary> {
  const bytes = await file.arrayBuffer();
  return apiFetch("/api/admin/media", mediaAssetSummarySchema, {
    method: "POST",
    body: bytes,
    headers: { "content-type": file.type },
  });
}
