import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { adminKeys, deactivateUser } from "@/entities/admin";
import { ApiFetchError } from "@/shared/api";
import type { UserView } from "@/shared/api/generated/admin";
import { adminErrorMessage } from "@/shared/lib/admin-errors";
import { Banner, Button } from "@/shared/ui";

/**
 * §10.5: deactivation is not a soft flag — it kills every session for this
 * user immediately and closes their open socket with 4401 (§7's reason for
 * opaque tokens over JWTs in the first place). An admin who thinks this is
 * reversible-and-gentle will use it on a live player mid-game, so the
 * confirmation step (same two-click shape as `SurrenderButton`, the one
 * other irreversible action in this codebase) says exactly that before the
 * second click sends anything.
 *
 * `self_target` (you cannot deactivate your own account) is the one
 * refusal this control's mutation can produce — the backend decides that,
 * not this component; there is no client-side "is this me" check.
 */
export function DeactivateControl({ user }: { user: UserView }) {
  const queryClient = useQueryClient();
  const [confirming, setConfirming] = useState(false);
  const mutation = useMutation({
    mutationFn: () => deactivateUser(user.id),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: adminKeys.users() });
      setConfirming(false);
    },
  });
  const error = mutation.error instanceof ApiFetchError ? mutation.error : null;

  if (!confirming) {
    return (
      <Button variant="ghost" onClick={() => setConfirming(true)}>
        Deactivate
      </Button>
    );
  }

  return (
    <div className="flex flex-col items-start gap-2">
      <span className="text-[11px] font-medium text-ink-dim">
        Deactivating {user.username} signs them out everywhere immediately — every open session ends
        and their connection is closed right away. This cannot be undone from here.
      </span>
      <div className="flex items-center gap-2">
        <Button variant="ghost" disabled={mutation.isPending} onClick={() => mutation.mutate()}>
          {mutation.isPending ? "Deactivating…" : "Confirm deactivate"}
        </Button>
        <Button
          variant="ghost"
          disabled={mutation.isPending}
          onClick={() => {
            mutation.reset();
            setConfirming(false);
          }}
        >
          Cancel
        </Button>
      </div>
      {error !== null && (
        <Banner tone="bad" {...(error.code !== null ? { code: error.code } : {})}>
          {adminErrorMessage(error.code ?? "validation_failed", error.message)}
        </Banner>
      )}
    </div>
  );
}
