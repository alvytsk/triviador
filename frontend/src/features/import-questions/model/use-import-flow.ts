import { useMutation } from "@tanstack/react-query";
import { useState } from "react";
import { confirmImport, dryRunImport, fetchRejectedCsv } from "@/entities/admin";
import { ApiFetchError } from "@/shared/api";
import type { ImportSummary } from "@/shared/api/generated/admin";
import { adminErrorMessage } from "@/shared/lib/admin-errors";

/**
 * The recipe browsers use to hand the user a file that came back from
 * `fetch` rather than from a real `<a href>` navigation: a Blob, an object
 * URL, a detached anchor with `download` set, one synthetic click. jsdom
 * 30 has no `URL.createObjectURL` of its own (`testing/setup.ts`'s
 * comment), which is why that stub exists — this function is otherwise
 * exactly what runs in a real browser too.
 */
function triggerCsvDownload(filename: string, csv: string) {
  const blob = new Blob([csv], { type: "text/csv" });
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = filename;
  document.body.appendChild(anchor);
  anchor.click();
  document.body.removeChild(anchor);
  URL.revokeObjectURL(url);
}

function errorMessage(error: unknown): string | null {
  if (!(error instanceof ApiFetchError)) return null;
  return adminErrorMessage(error.code ?? "validation_failed", error.message);
}

export interface ImportFlow {
  /** The last dry-run or confirm response. `null` before any upload, and
   *  also `null` right after a reload — see `isResuming`. */
  summary: ImportSummary | null;
  /** `importId` survived a reload (it rides the URL, §10.3's durable
   *  handle) but `summary` did not — there is no `GET` for an import's
   *  report (`imports.py` exposes only `dry-run`, `rejected.csv` and
   *  `confirm`), so this screen cannot show the per-row report again. It
   *  can still let the admin confirm the import or abandon it. */
  isResuming: boolean;
  isUploading: boolean;
  uploadError: string | null;
  isConfirming: boolean;
  confirmError: string | null;
  isDownloading: boolean;
  downloadError: string | null;
  upload: (file: File) => void;
  confirm: () => void;
  downloadRejected: () => void;
  startOver: () => void;
}

/**
 * One hook for both of §10.3's phases, because the screen is one screen
 * with visible state, not a route per phase — `startOver` is the only way
 * back to the upload step, and reaching it never requires losing the URL's
 * `importId` on a whim.
 *
 * `importId`/`onImportIdChange` are owned by the caller (the page, bound
 * to the route's `validateSearch`) rather than kept in local state here:
 * the URL is what survives a reload, and a hook-local `useState` would
 * not — the exact case `isResuming` exists to handle.
 */
export function useImportFlow(
  importId: string | undefined,
  onImportIdChange: (importId: string | undefined) => void,
): ImportFlow {
  const [summary, setSummary] = useState<ImportSummary | null>(null);

  const dryRun = useMutation({
    mutationFn: (file: File) => dryRunImport(file),
    onSuccess: (result) => {
      setSummary(result);
      onImportIdChange(result.import_id);
    },
  });

  const activeImportId = summary?.import_id ?? importId;

  const confirmMutation = useMutation({
    mutationFn: () => {
      if (activeImportId === undefined) {
        throw new Error("no import to confirm");
      }
      return confirmImport(activeImportId);
    },
    onSuccess: (result) => setSummary(result),
  });

  const download = useMutation({
    mutationFn: async () => {
      if (activeImportId === undefined) {
        throw new Error("no import to download rejections from");
      }
      const csv = await fetchRejectedCsv(activeImportId);
      triggerCsvDownload(`${activeImportId}-rejected.csv`, csv);
    },
  });

  function startOver() {
    setSummary(null);
    dryRun.reset();
    confirmMutation.reset();
    download.reset();
    onImportIdChange(undefined);
  }

  return {
    summary,
    isResuming: importId !== undefined && summary === null,
    isUploading: dryRun.isPending,
    uploadError: errorMessage(dryRun.error),
    isConfirming: confirmMutation.isPending,
    confirmError: errorMessage(confirmMutation.error),
    isDownloading: download.isPending,
    downloadError: errorMessage(download.error),
    upload: (file) => dryRun.mutate(file),
    confirm: () => confirmMutation.mutate(),
    downloadRejected: () => download.mutate(),
    startOver,
  };
}
