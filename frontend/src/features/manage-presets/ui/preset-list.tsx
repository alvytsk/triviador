import type { PresetDetail } from "@/shared/api/generated/admin";
import {
  Button,
  Chip,
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/shared/ui";
import { RetireControl } from "./retire-control";

export interface PresetListProps {
  presets: PresetDetail[];
  selectedId: string | null;
  onSelect: (id: string) => void;
}

/**
 * §10.6: every preset, retired ones included. Plan 7A's
 * `get_including_retired` (its repository method, and the admin route's
 * `GET /{preset_id}` / `/coverage` built on top of it) exists precisely so
 * this list can show a retired row and let an admin open it — there is
 * deliberately no reactivation route (§6.1's soft delete is one-way), so
 * "open" here means read access, not a way to bring it back.
 *
 * A retired preset already has nothing left to retire — same reasoning as
 * `UserTable`'s `DeactivateControl`, which withdraws itself once
 * `is_active` is false rather than offering a click with no further
 * effect — so `RetireControl` only renders for an active row.
 */
export function PresetList({ presets, selectedId, onSelect }: PresetListProps) {
  return (
    <Table>
      <TableHeader>
        <TableRow>
          <TableHead>Name</TableHead>
          <TableHead>Status</TableHead>
          <TableHead />
          <TableHead>Actions</TableHead>
        </TableRow>
      </TableHeader>
      <TableBody>
        {presets.map((preset) => (
          <TableRow key={preset.id} aria-selected={preset.id === selectedId}>
            <TableCell>{preset.name}</TableCell>
            <TableCell>
              <div className="flex gap-2">
                {preset.is_default && <Chip>Default</Chip>}
                <Chip
                  className={preset.is_active ? "bg-good text-base" : "bg-track text-ink-faint"}
                >
                  {preset.is_active ? "Active" : "Retired"}
                </Chip>
              </div>
            </TableCell>
            <TableCell>
              <Button
                variant="ghost"
                aria-label={`Open ${preset.name}`}
                onClick={() => onSelect(preset.id)}
              >
                Open
              </Button>
            </TableCell>
            <TableCell>{preset.is_active && <RetireControl preset={preset} />}</TableCell>
          </TableRow>
        ))}
      </TableBody>
    </Table>
  );
}
