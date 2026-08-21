import { useMutation, useQueryClient } from "@tanstack/react-query";
import { adminKeys, revokeInvite } from "@/entities/admin";
import { ApiFetchError } from "@/shared/api";
import type { InviteView } from "@/shared/api/generated/admin";
import { adminErrorMessage } from "@/shared/lib/admin-errors";
import {
  Banner,
  Button,
  Chip,
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/shared/ui";

/**
 * §10.5's listing never asks for, or shows, a code — hashing a code the
 * server could hand back on request would be decorative. This is true by
 * construction: `InviteView` (unlike `IssuedInvite`) carries no `code`
 * field at all. Same idiom `_authed.admin.questions.index.tsx` uses for
 * `AssertExhaustive`: `AssertNotAKey<"code", InviteView>` resolves to the
 * literal type `true` today; the moment `"code"` becomes a real key of
 * `InviteView` again, it resolves to `false` and the assignment below
 * fails to compile — the assertion documents the intent, not just today's
 * shape.
 */
type AssertNotAKey<K extends string, Obj> = K extends keyof Obj ? false : true;
const _inviteViewNeverCarriesCode: AssertNotAKey<"code", InviteView> = true;
void _inviteViewNeverCarriesCode;

const STATUS_LABEL: Record<InviteView["status"], string> = {
  pending: "Pending",
  used: "Used",
  revoked: "Revoked",
  expired: "Expired",
};

const STATUS_CLASS: Record<InviteView["status"], string> = {
  pending: "",
  used: "bg-good text-base",
  revoked: "bg-bad text-ink",
  expired: "bg-track text-ink-faint",
};

function formatExpiry(iso: string): string {
  return new Date(iso).toLocaleString();
}

/**
 * `invite` is a plain prop, not something this row re-reads from its own
 * query — the parent (`InvitesPage`) owns the one `useQuery(adminInvitesQueryOptions())`
 * that produces it. Revoking is still this row's own `useMutation`, though:
 * §10.5 Decision — "revoking twice is success, not an error" (the backend
 * answers 200 both times) — so the Revoke button stays enabled the moment
 * the mutation settles, whether it succeeded or not, rather than latching
 * into a disabled/error state that would make a second, harmless click
 * impossible.
 */
function InviteRow({ invite }: { invite: InviteView }) {
  const queryClient = useQueryClient();
  const revoke = useMutation({
    mutationFn: () => revokeInvite(invite.id),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: adminKeys.invites() });
    },
  });
  const error = revoke.error instanceof ApiFetchError ? revoke.error : null;

  return (
    <TableRow>
      <TableCell>
        <Chip className={STATUS_CLASS[invite.status]}>{STATUS_LABEL[invite.status]}</Chip>
      </TableCell>
      <TableCell>{formatExpiry(invite.expires_at)}</TableCell>
      <TableCell>{invite.used_by ?? "—"}</TableCell>
      <TableCell>
        {invite.status === "pending" && (
          <div className="flex flex-col items-start gap-2">
            <Button variant="ghost" disabled={revoke.isPending} onClick={() => revoke.mutate()}>
              Revoke
            </Button>
            {error !== null && (
              <Banner tone="bad" {...(error.code !== null ? { code: error.code } : {})}>
                {adminErrorMessage(error.code ?? "validation_failed", error.message)}
              </Banner>
            )}
          </div>
        )}
      </TableCell>
    </TableRow>
  );
}

/** §10.5's list: status, expiry, and who redeemed — nothing an admin could
 *  screenshot into a working invite code. */
export function InviteTable({ invites }: { invites: InviteView[] }) {
  return (
    <Table>
      <TableHeader>
        <TableRow>
          <TableHead>Status</TableHead>
          <TableHead>Expires</TableHead>
          <TableHead>Redeemed by</TableHead>
          <TableHead>Actions</TableHead>
        </TableRow>
      </TableHeader>
      <TableBody>
        {invites.map((invite) => (
          <InviteRow key={invite.id} invite={invite} />
        ))}
      </TableBody>
    </Table>
  );
}
