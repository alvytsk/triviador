import { useQuery } from "@tanstack/react-query";
import { adminPresetCoverageQueryOptions } from "@/entities/admin";
import { Banner, Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/shared/ui";

export interface CoveragePanelProps {
  presetId: string;
}

const KIND_LABEL: Record<string, string> = {
  numeric: "Numeric",
  multiple_choice: "Multiple choice",
};

/**
 * §10.6's need-vs-bank table, computed server-side from
 * `required_question_budget(rules)` — the same function `StartGame` uses,
 * so the "need" side can never disagree between this panel and the real
 * check. The "bank" side can: an admin can deactivate a question between
 * reading this panel and starting a game, which is exactly why this is
 * informative, not authoritative (§10.6's three checkpoints — this page,
 * `CreateGame`, and the authoritative `StartGame`/`PoolDrawn`).
 *
 * That sentence is gated on the contract's own `informative` field
 * (`PresetCoverage.informative`, always `true` per its docstring in
 * `api/schemas/admin/presets.py`) rather than shown unconditionally —
 * the field exists so this component has something server-supplied to
 * render the claim from, instead of inventing wording nothing upstream
 * actually asserts.
 *
 * The per-kind ✓/✗ is a direct comparison of the two numbers this same
 * response already carries for that row (`bank[kind] >= required[kind]`)
 * — not an independently invented rule. The aggregate "can this start"
 * verdict is rendered from `sufficient` exactly as the server computed
 * it, never recomputed by ANDing the per-row checks here.
 */
export function CoveragePanel({ presetId }: CoveragePanelProps) {
  const coverage = useQuery(adminPresetCoverageQueryOptions(presetId));

  if (coverage.isPending) {
    return <p className="text-[13px] text-ink-dim">Loading coverage…</p>;
  }
  if (coverage.isError) {
    return <Banner tone="bad">Could not load coverage. Try again.</Banner>;
  }

  const data = coverage.data;
  const kinds = Object.keys(data.required);

  return (
    <div className="flex flex-col gap-4">
      <Table>
        <TableHeader>
          <TableRow>
            <TableHead>Kind</TableHead>
            <TableHead>Need</TableHead>
            <TableHead>Bank</TableHead>
            <TableHead />
          </TableRow>
        </TableHeader>
        <TableBody>
          {kinds.map((kind) => {
            const need = data.required[kind] ?? 0;
            const bank = data.bank[kind] ?? 0;
            return (
              <TableRow key={kind}>
                <TableCell>{KIND_LABEL[kind] ?? kind}</TableCell>
                <TableCell>{need}</TableCell>
                <TableCell>{bank}</TableCell>
                <TableCell>{bank >= need ? "✓" : "✗"}</TableCell>
              </TableRow>
            );
          })}
        </TableBody>
      </Table>

      <Banner tone={data.sufficient ? "quiet" : "warn"}>
        {data.sufficient
          ? "The bank currently covers this preset."
          : "The bank does not currently cover this preset — a game could not start right now."}
      </Banner>

      {data.informative && (
        <Banner tone="quiet">
          This is informative, not authoritative — an admin can deactivate a question between
          reading this panel and starting a game. StartGame makes the real check, from the same view
          the immutable question pool is drawn from.
        </Banner>
      )}
    </div>
  );
}
