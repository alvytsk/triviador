import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { adminPresetsQueryOptions } from "@/entities/admin";
import { CoveragePanel, PresetForm, PresetList } from "@/features/manage-presets";
import type { PresetDetail } from "@/shared/api/generated/admin";
import { Banner, Button } from "@/shared/ui";

/**
 * §10.6: a list with the default marked (retired presets included — Plan
 * 7A's `get_including_retired` exists precisely so this screen can show
 * one; there is deliberately no reactivation route, §6.1's soft delete is
 * one-way), a rules form, and a coverage panel.
 *
 * Selection lives in this page's own state, not the URL: Task 8 registers
 * one route, `/admin/presets`, with no `$id` child (`UsersPage` made the
 * same call for the same reason) — `adminPresetsQueryOptions` already
 * returns full `PresetDetail` rows (rules included) for every preset, so
 * there is nothing a per-id fetch would add, and nothing here durable
 * enough to be worth bookmarking that the list doesn't already carry.
 */
export function PresetsPage() {
  const presets = useQuery(adminPresetsQueryOptions());
  const [selectedId, setSelectedId] = useState<string | "new" | null>(null);

  const selected =
    selectedId !== null && selectedId !== "new"
      ? presets.data?.find((preset) => preset.id === selectedId)
      : undefined;

  function handleSaved(saved: PresetDetail) {
    setSelectedId(saved.id);
  }

  return (
    <div className="flex flex-col gap-6">
      <div className="flex items-center justify-between gap-4">
        <h1 className="font-display text-3xl tracking-wider text-gold">Presets</h1>
        <Button onClick={() => setSelectedId("new")}>New preset</Button>
      </div>

      {presets.isError ? (
        <Banner tone="bad">Could not load presets. Try again.</Banner>
      ) : presets.isPending ? (
        <p className="text-[13px] text-ink-dim">Loading…</p>
      ) : (
        <PresetList
          presets={presets.data}
          selectedId={selectedId === "new" ? null : selectedId}
          onSelect={setSelectedId}
        />
      )}

      {selectedId === "new" && <PresetForm onSaved={handleSaved} />}
      {selected !== undefined && (
        <>
          <PresetForm preset={selected} onSaved={handleSaved} />
          <CoveragePanel presetId={selected.id} />
        </>
      )}
    </div>
  );
}
