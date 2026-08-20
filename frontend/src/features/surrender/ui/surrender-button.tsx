import { useState } from "react";
import { Banner, Button } from "@/shared/ui";
import { useSurrender } from "../model/use-surrender";

/**
 * The one irreversible thing a player can do (the brief's own words), so
 * one click never sends it. The first click only reveals a second, explicit
 * "Confirm surrender" — cancellable right up until that second click — and
 * only that second click builds and sends the frame.
 */
export function SurrenderButton({ gameId }: { gameId: string }) {
  const { surrender, isSending, failure } = useSurrender(gameId);
  const [confirming, setConfirming] = useState(false);

  if (!confirming) {
    return (
      <Button variant="ghost" onClick={() => setConfirming(true)}>
        Surrender
      </Button>
    );
  }

  return (
    <div className="flex flex-col items-end gap-2">
      <div className="flex items-center gap-2">
        <span className="text-[11px] font-medium text-ink-dim">
          Surrender? You cannot undo this.
        </span>
        <Button disabled={isSending} onClick={surrender}>
          {isSending ? "Surrendering…" : "Confirm surrender"}
        </Button>
        <Button variant="ghost" disabled={isSending} onClick={() => setConfirming(false)}>
          Cancel
        </Button>
      </div>
      {failure !== null && (
        <Banner tone="bad" code={failure.code}>
          {failure.message}
        </Banner>
      )}
    </div>
  );
}
