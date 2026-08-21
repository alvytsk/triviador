import { useMutation } from "@tanstack/react-query";
import { useState } from "react";
import { uploadMedia } from "@/entities/admin";
import { ApiFetchError } from "@/shared/api";
import { adminErrorMessage } from "@/shared/lib/admin-errors";
import { Button } from "@/shared/ui";

/**
 * §10.4: no separate media browser. The field uploads through
 * `uploadMedia(file)` itself and hands the returned asset id up to the
 * form via `onChange` — the thumbnail comes from the same response
 * (`MediaAssetSummary.url`), kept in local state here rather than on the
 * form, since the write request only ever carries the id.
 *
 * A rejected upload (415 `media_rejected`) sets `error` from this
 * component's own `useMutation` and calls `onChange` for nothing else —
 * every other field on the form is untouched, because nothing here ever
 * reaches into the form beyond this one field's setter.
 *
 * Editing a question that already has `media_asset_id` but was loaded
 * from `QuestionDetail` (which carries no `url`) shows a plain "Media
 * attached" label instead of a thumbnail — there is no `GET` for a single
 * asset's summary on this slice's surface (`entities/admin`'s `media.ts`
 * only exports `uploadMedia`), so a thumbnail for pre-existing media is
 * only ever available once this session re-uploads it.
 */
export function MediaField({
  mediaAssetId,
  onChange,
}: {
  mediaAssetId: string | null;
  onChange: (id: string | null) => void;
}) {
  const [previewUrl, setPreviewUrl] = useState<string | null>(null);

  const upload = useMutation({
    mutationFn: (file: File) => uploadMedia(file),
    onSuccess: (asset) => {
      setPreviewUrl(asset.url);
      onChange(asset.id);
    },
  });

  const error =
    upload.error instanceof ApiFetchError
      ? adminErrorMessage(upload.error.code ?? "validation_failed", upload.error.message)
      : null;

  return (
    <div className="flex flex-col gap-2">
      <span className="text-[10px] font-semibold uppercase tracking-[0.14em] text-ink-dim">
        Media
      </span>

      {mediaAssetId !== null && (
        <div className="flex items-center gap-3">
          {previewUrl !== null ? (
            <img src={previewUrl} alt="" className="h-16 w-16 border-2 border-line object-cover" />
          ) : (
            <span className="text-[13px] text-ink-dim">Media attached</span>
          )}
          <Button
            type="button"
            variant="ghost"
            onClick={() => {
              setPreviewUrl(null);
              onChange(null);
            }}
          >
            Remove
          </Button>
        </div>
      )}

      <input
        type="file"
        accept="image/*"
        aria-label="Upload media"
        disabled={upload.isPending}
        onChange={(event) => {
          const file = event.target.files?.[0];
          event.target.value = "";
          if (file !== undefined) upload.mutate(file);
        }}
      />

      {error !== null && <p className="text-[11px] font-medium text-bad">{error}</p>}
    </div>
  );
}
