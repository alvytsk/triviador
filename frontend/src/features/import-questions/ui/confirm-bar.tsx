import type { ImportSummary } from "@/shared/api/generated/admin";
import { Banner, Button } from "@/shared/ui";

/**
 * `summary.confirmable` drives the button's `disabled`, never a
 * recomputation of `rejected_count === 0` — `generated/admin.ts`'s own
 * comment on `importSummarySchema` says why: the rule also depends on
 * status and expiry, and a client that re-derives it will eventually
 * derive it differently. The failure mode is a live CONFIRM button on a
 * dead import.
 */
export function ConfirmBar({
  summary,
  isConfirming,
  confirmError,
  isDownloading,
  onConfirm,
  onDownloadRejected,
}: {
  summary: ImportSummary;
  isConfirming: boolean;
  confirmError: string | null;
  isDownloading: boolean;
  onConfirm: () => void;
  onDownloadRejected: () => void;
}) {
  if (summary.status === "confirmed") {
    return <Banner tone="quiet">Confirmed — the questions were added to the bank.</Banner>;
  }

  return (
    <div className="flex flex-col gap-3">
      <div className="flex items-center gap-3">
        <Button onClick={onConfirm} disabled={!summary.confirmable || isConfirming}>
          {isConfirming ? "Confirming…" : "Confirm import"}
        </Button>
        {summary.rejected_count > 0 && (
          <Button variant="ghost" onClick={onDownloadRejected} disabled={isDownloading}>
            {isDownloading ? "Downloading…" : "Download rejected rows"}
          </Button>
        )}
      </div>
      {confirmError !== null && <Banner tone="bad">{confirmError}</Banner>}
    </div>
  );
}
