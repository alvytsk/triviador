import { Banner, Button } from "@/shared/ui";
import { useImportFlow } from "../model/use-import-flow";
import { ConfirmBar } from "./confirm-bar";
import { ReportTable } from "./report-table";
import { UploadStep } from "./upload-step";

export interface ImportWizardProps {
  importId: string | undefined;
  onImportIdChange: (importId: string | undefined) => void;
}

/**
 * §10.3's two phases as one screen, not a route per phase: the dry-run and
 * the confirm both land here, and `flow.isResuming` is what a reload
 * mid-flow lands on — `use-import-flow.ts`'s own comment explains why the
 * per-row report cannot come back, only the ability to confirm or bail.
 */
export function ImportWizard({ importId, onImportIdChange }: ImportWizardProps) {
  const flow = useImportFlow(importId, onImportIdChange);

  if (flow.isResuming) {
    return (
      <div className="flex flex-col gap-4 border-2 border-line bg-panel px-6 py-8">
        <p className="text-[14px] text-ink">
          Resuming import {importId}. The per-row report from the dry-run is gone, but this import
          can still be confirmed, or abandoned in favor of a fresh upload.
        </p>
        <div className="flex items-center gap-3">
          <Button onClick={flow.confirm} disabled={flow.isConfirming}>
            {flow.isConfirming ? "Confirming…" : "Confirm import"}
          </Button>
          <Button variant="ghost" onClick={flow.startOver}>
            Start a new upload
          </Button>
        </div>
        {flow.confirmError !== null && <Banner tone="bad">{flow.confirmError}</Banner>}
      </div>
    );
  }

  return (
    <div className="flex flex-col gap-6">
      <UploadStep onSelect={flow.upload} isUploading={flow.isUploading} error={flow.uploadError} />

      {flow.summary !== null && (
        <>
          <ReportTable summary={flow.summary} />
          <ConfirmBar
            summary={flow.summary}
            isConfirming={flow.isConfirming}
            confirmError={flow.confirmError}
            isDownloading={flow.isDownloading}
            onConfirm={flow.confirm}
            onDownloadRejected={flow.downloadRejected}
          />
          {flow.downloadError !== null && <Banner tone="bad">{flow.downloadError}</Banner>}
          <Button variant="ghost" className="self-start" onClick={flow.startOver}>
            Start over
          </Button>
        </>
      )}
    </div>
  );
}
