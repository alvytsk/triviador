import { useQuery } from "@tanstack/react-query";
import { adminInvitesQueryOptions } from "@/entities/admin";
import { InviteTable, IssueDialog } from "@/features/manage-invites";
import { Banner } from "@/shared/ui";

/** §10.5: issue N codes with an expiry, list with status, revoke. Redemption
 *  itself is the public `POST /auth/redeem` — nothing here. */
export function InvitesPage() {
  const invites = useQuery(adminInvitesQueryOptions());

  return (
    <div className="flex flex-col gap-6">
      <div className="flex items-center justify-between gap-4">
        <h1 className="font-display text-3xl tracking-wider text-gold">Invites</h1>
        <IssueDialog />
      </div>

      {invites.isError ? (
        <Banner tone="bad">Could not load invites. Try again.</Banner>
      ) : invites.isPending ? (
        <p className="text-[13px] text-ink-dim">Loading…</p>
      ) : invites.data.length === 0 ? (
        <div className="flex flex-col items-start gap-3 border-2 border-line bg-panel px-6 py-8">
          <p className="text-[14px] text-ink">No invites yet — issue some to get started.</p>
        </div>
      ) : (
        <InviteTable invites={invites.data} />
      )}
    </div>
  );
}
