import { useSocket } from "@/shared/api";
import { TIMING } from "@/shared/config";
import { cn } from "@/shared/lib";
import { useDeadline } from "../model/use-deadline";

/**
 * §8.3: presentation only. Renders from `deadline_at` plus the measured
 * ping/pong clock offset (`useSocket().offsetMs`) and never sends anything,
 * marks an answer late, or tells the server time is up — `ctx.now >=
 * deadline_at` on the server stays the only authority. Below
 * `TIMING.TIMER_URGENT_MS` it turns red, which is decoration, not a signal
 * anything else in the app reacts to.
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
