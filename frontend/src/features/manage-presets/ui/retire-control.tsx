import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { adminKeys, deactivatePreset } from "@/entities/admin";
import { ApiFetchError } from "@/shared/api";
import type { PresetDetail } from "@/shared/api/generated/admin";
import { adminErrorMessage } from "@/shared/lib/admin-errors";
import { Banner, Button } from "@/shared/ui";

/**
 * §10.6's missing D: `DELETE /api/admin/presets/{id}` is §6.1's soft
 * deactivation, already exported and tested (`entities/admin/api/
 * presets.ts`'s `deactivatePreset`) but never wired to a control until
 * now.
 *
 * Two-step confirm, same shape as `DeactivateControl` in
 * `features/manage-users` — the established irreversible-action pattern
 * in this codebase (also `SurrenderButton`'s). Retiring is one-way: this
 * module's docstring on `presets.py` says so explicitly ("no reactivation
 * route... deliberately, not an oversight"), so the confirmation copy
 * says so too, before the second click sends anything.
 *
 * The one refusal this control's mutation can produce is `default_preset`
 * — a THIRD distinct sentence for that code, different from both of
 * `PresetForm`'s PATCH refusals (`api/http/admin/presets.py`'s
 * `deactivate_preset` route, `DeactivateOutcome.IS_DEFAULT`: "this is the
 * default preset; make another one default first"). The backend decides
 * that, not this component — there is no client-side "is this the
 * default" check; `adminErrorMessage` falls through to the server's own
 * message for this code (see `shared/lib/admin-errors.ts`).
 */
export function RetireControl({ preset }: { preset: PresetDetail }) {
  const queryClient = useQueryClient();
  const [confirming, setConfirming] = useState(false);
  const mutation = useMutation({
    mutationFn: () => deactivatePreset(preset.id),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: adminKeys.presets() });
      setConfirming(false);
    },
  });
  const error = mutation.error instanceof ApiFetchError ? mutation.error : null;

  if (!confirming) {
    return (
      <Button variant="ghost" onClick={() => setConfirming(true)}>
        Retire
      </Button>
    );
  }

  return (
    <div className="flex flex-col items-start gap-2">
      <span className="text-[11px] font-medium text-ink-dim">
        Retiring {preset.name} takes it out of the pool new games can be started from. This cannot
        be undone from here — there is no reactivation route; a retired preset stays visible and can
        still be opened, but not brought back to active.
      </span>
      <div className="flex items-center gap-2">
        <Button variant="ghost" disabled={mutation.isPending} onClick={() => mutation.mutate()}>
          {mutation.isPending ? "Retiring…" : "Confirm retire"}
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
