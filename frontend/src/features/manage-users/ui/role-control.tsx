import { useMutation, useQueryClient } from "@tanstack/react-query";
import { adminKeys, setUserRole } from "@/entities/admin";
import { ApiFetchError } from "@/shared/api";
import type { UserView } from "@/shared/api/generated/admin";
import { adminErrorMessage } from "@/shared/lib/admin-errors";
import { Banner, Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/shared/ui";

const ROLE_LABEL: Record<UserView["role"], string> = {
  player: "Player",
  admin: "Admin",
};

/**
 * §10.5: "cannot demote the last admin" — the backend, not this control,
 * decides that. Whoever is the last admin gets `last_admin` back on their
 * own attempt to demote *anyone* (Plan 7A's Task 11), including someone
 * else's row, so there is deliberately no client-side guess at who counts
 * as "the last one" here — the brief's own resolved ambiguity is that no
 * self-demotion block belongs in this component. This control just sends
 * the change and renders whatever the server says.
 */
export function RoleControl({ user }: { user: UserView }) {
  const queryClient = useQueryClient();
  const mutation = useMutation({
    mutationFn: (role: UserView["role"]) => setUserRole(user.id, { role }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: adminKeys.users() });
    },
  });
  const error = mutation.error instanceof ApiFetchError ? mutation.error : null;
  // `mutation.data` — the backend's own updated `UserView`, once this
  // resolves — over the `user` prop: `UserTable`'s caller (`UsersPage`)
  // re-renders it with fresh props once the invalidated query above
  // refetches, but that round trip is not this component's to wait on.
  // Falling back to the response the server already sent is what makes
  // "shows the new role after a successful change" true immediately,
  // not just eventually.
  const displayedRole = mutation.data?.role ?? user.role;

  return (
    <div className="flex flex-col items-start gap-2">
      <Select
        value={displayedRole}
        onValueChange={(value) => mutation.mutate(value as UserView["role"])}
      >
        <SelectTrigger aria-label={`Role for ${user.username}`} className="w-32">
          <SelectValue>{ROLE_LABEL[displayedRole]}</SelectValue>
        </SelectTrigger>
        <SelectContent>
          <SelectItem value="player">Player</SelectItem>
          <SelectItem value="admin">Admin</SelectItem>
        </SelectContent>
      </Select>
      {error !== null && (
        <Banner tone="bad" {...(error.code !== null ? { code: error.code } : {})}>
          {adminErrorMessage(error.code ?? "validation_failed", error.message)}
        </Banner>
      )}
    </div>
  );
}
