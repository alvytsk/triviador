import type { UserView } from "@/shared/api/generated/admin";
import { Chip, Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/shared/ui";
import { DeactivateControl } from "./deactivate-control";
import { RoleControl } from "./role-control";

/** An already-inactive user has nothing left to deactivate — the backend
 *  route exists to *cause* the transition, not to be clicked again on a
 *  user it has no further effect on (unlike `InviteTable`'s revoke, which
 *  §10.5 explicitly makes idempotent, deactivate has no such decision on
 *  the record, so the control is withdrawn instead of guessing). Role
 *  changes stay available either way — reactivating happens by promoting
 *  or demoting, not through this screen (§10.5 lists no "reactivate"
 *  route). */
function UserRow({ user }: { user: UserView }) {
  return (
    <TableRow>
      <TableCell>{user.username}</TableCell>
      <TableCell>{user.display_name}</TableCell>
      <TableCell>
        <RoleControl user={user} />
      </TableCell>
      <TableCell>
        <Chip className={user.is_active ? "bg-good text-base" : "bg-track text-ink-faint"}>
          {user.is_active ? "Active" : "Inactive"}
        </Chip>
      </TableCell>
      <TableCell>{user.is_active && <DeactivateControl user={user} />}</TableCell>
    </TableRow>
  );
}

/** §10.5: username, display name, role and active state, with a role
 *  control and a deactivate control per row. */
export function UserTable({ users }: { users: UserView[] }) {
  return (
    <Table>
      <TableHeader>
        <TableRow>
          <TableHead>Username</TableHead>
          <TableHead>Display name</TableHead>
          <TableHead>Role</TableHead>
          <TableHead>Status</TableHead>
          <TableHead>Actions</TableHead>
        </TableRow>
      </TableHeader>
      <TableBody>
        {users.map((user) => (
          <UserRow key={user.id} user={user} />
        ))}
      </TableBody>
    </Table>
  );
}
