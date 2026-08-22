import { useNavigate, useSearch } from "@tanstack/react-router";
import { ImportWizard } from "@/features/import-questions";

/**
 * Route config lives in the eager `_authed.admin.questions.import.tsx`;
 * this lazy page is where `importId` is actually read and written back —
 * the same split every other admin page in this plan uses, and for the
 * same reason: a `loader` in the eager file would drag `entities/admin`
 * into the player bundle.
 *
 * `setImportId` always `replace: true`s: `importId` records "which import
 * is this screen about" rather than a step in a history a back button
 * should walk back through, so every upload's id overwrites the last
 * rather than piling up entries.
 */
export function ImportPage() {
  const search = useSearch({ from: "/_authed/admin/questions/import" });
  const navigate = useNavigate({ from: "/admin/questions/import" });

  function setImportId(importId: string | undefined) {
    navigate({ to: "/admin/questions/import", search: { importId }, replace: true });
  }

  return (
    <div className="flex flex-col gap-6">
      <h1 className="font-display text-3xl tracking-wider text-gold">Import questions</h1>
      <ImportWizard importId={search.importId} onImportIdChange={setImportId} />
    </div>
  );
}
