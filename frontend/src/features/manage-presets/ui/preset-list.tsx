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
 */
export function PresetList({ presets, selectedId, onSelect }: PresetListProps) {
  return (
    <Table>
      <TableHeader>
        <TableRow>
          <TableHead>Name</TableHead>
          <TableHead>Status</TableHead>
          <TableHead />
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
          </TableRow>
        ))}
      </TableBody>
    </Table>
  );
}
