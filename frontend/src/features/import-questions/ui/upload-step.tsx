import { Banner } from "@/shared/ui";

/**
 * No separate "run the dry-run" button — selecting a file runs it
 * immediately, the same auto-submit `MediaField` (Task 4) uses for its own
 * single-purpose upload. `event.target.value = ""` afterwards lets the
 * same file be picked again (e.g. after fixing it and re-exporting under
 * the same name).
 */
export function UploadStep({
  onSelect,
  isUploading,
  error,
}: {
  onSelect: (file: File) => void;
  isUploading: boolean;
  error: string | null;
}) {
  return (
    <div className="flex flex-col gap-2">
      <label
        htmlFor="import-file"
        className="text-[10px] font-semibold uppercase tracking-[0.14em] text-ink-dim"
      >
        Upload a question file
      </label>
      <input
        id="import-file"
        type="file"
        accept=".csv,.zip"
        aria-label="Upload questions file"
        disabled={isUploading}
        onChange={(event) => {
          const file = event.target.files?.[0];
          event.target.value = "";
          if (file !== undefined) onSelect(file);
        }}
      />
      {isUploading && <p className="text-[13px] text-ink-dim">Checking the file…</p>}
      {error !== null && <Banner tone="bad">{error}</Banner>}
    </div>
  );
}
