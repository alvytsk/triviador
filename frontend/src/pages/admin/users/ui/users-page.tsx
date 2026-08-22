import { useQuery } from "@tanstack/react-query";
import { adminUsersQueryOptions } from "@/entities/admin";
import { UserTable } from "@/features/manage-users";
import { Banner } from "@/shared/ui";

/** §10.5: list every user, grant/revoke admin, deactivate. There is no
 *  create-user flow here — accounts come from invite redemption
 *  (`InvitesPage`'s own domain), not from this screen. */
export function UsersPage() {
  const users = useQuery(adminUsersQueryOptions());

  return (
    <div className="flex flex-col gap-6">
      <h1 className="font-display text-3xl tracking-wider text-gold">Users</h1>

      {users.isError ? (
        <Banner tone="bad">Could not load users. Try again.</Banner>
      ) : users.isPending ? (
        <p className="text-[13px] text-ink-dim">Loading…</p>
      ) : (
        <UserTable users={users.data} />
      )}
    </div>
  );
}
