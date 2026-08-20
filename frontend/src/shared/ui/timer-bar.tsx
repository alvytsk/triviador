import { useSocket } from "../api/socket-context";
import { TIMING } from "../config";
import { cn } from "../lib/cn";
import { useDeadline } from "../lib/use-deadline";

/**
 * §8.3: presentation only. Renders from `deadline_at` plus the measured
 * ping/pong clock offset (`useSocket().offsetMs`) and never sends anything,
 * marks an answer late, or tells the server time is up — `ctx.now >=
 * deadline_at` on the server stays the only authority. Below
 * `TIMING.TIMER_URGENT_MS` it turns red, which is decoration, not a signal
 * anything else in the app reacts to.
 *
 * Lives in `shared/ui`, not `widgets/turn-dock/ui` where Task 12 first put
 * it: `widgets/question-dock`'s dock needs the identical clock (§9.5: "then
 * `<TimerBar>`") and steiger forbids one widget slice importing another's
 * internals (see `shared/lib/use-deadline.ts`'s doc comment for the same
 * move, one layer down). Both `widgets/turn-dock` and `widgets/question-dock`
 * import this directly from `@/shared/ui`; no re-export shim sits in either
 * slice.
 */
export function TimerBar({ deadlineAt }: { deadlineAt: string | null }) {
  const { offsetMs } = useSocket();
  const { remainingMs, expired } = useDeadline(deadlineAt, offsetMs);

  if (deadlineAt === null) return null;

  const seconds = Math.ceil(remainingMs / 1000);
  const urgent = remainingMs > 0 && remainingMs <= TIMING.TIMER_URGENT_MS;

  return (
    <div
      role="timer"
      aria-live="off"
      className={cn(
        "font-display text-3xl tracking-wider tabular-nums",
        expired ? "text-ink-faint" : urgent ? "text-bad" : "text-gold",
      )}
    >
      {expired ? "—" : `${seconds}s`}
    </div>
  );
}
