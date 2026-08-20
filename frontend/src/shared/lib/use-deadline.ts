import { useEffect, useRef, useState } from "react";

/**
 * §8.3 of Spec 1B: rendered from `deadline_at` plus the ping/pong offset,
 * driven by `requestAnimationFrame`, disabling input at the locally computed
 * deadline. **Presentation only** — the server's `ctx.now >= deadline_at`
 * stays authoritative, and this hook must never send anything, mark an
 * answer late, or tell the server that time is up.
 *
 * `expired` is therefore a UI affordance: it greys the dock. If the two
 * clocks disagree by 200 ms, the worst case is a player who could have
 * answered being stopped 200 ms early — which is the safe direction, and why
 * the offset is measured rather than assumed.
 *
 * Lives in `shared/lib`, not `widgets/turn-dock/model` where Task 12 first
 * put it: Task 13's `<QuestionDock>` (`widgets/question-dock`) needs it too,
 * for the same "time is up" disabling this doc comment describes, and
 * steiger's `fsd/forbidden-imports` refuses a cross-slice import between two
 * sibling widgets just as firmly as it refuses one layer reaching upward
 * (proved directly: `widgets/question-dock` importing this hook from
 * `@/widgets/turn-dock` trips "Forbidden cross-import from slice
 * \"turn-dock\"."). It has no widget-only dependency — only
 * `deadlineAt`/`offsetMs`, both plain values — so it moves down to `shared`,
 * the same "zero app-only dependency" move Task 12 made for
 * `useMediaPrefetch`. `widgets/turn-dock` no longer holds a copy of this
 * file or its test — a re-export whose only importer was its own test would
 * exist purely to avoid moving that test two directories, and this plan
 * deletes dead code on sight. `use-deadline.test.ts` lives beside this
 * module now, unchanged apart from the move.
 */
export function useDeadline(
  deadlineAt: string | null,
  offsetMs: () => number,
): { remainingMs: number; expired: boolean } {
  const [remainingMs, setRemaining] = useState(0);
  const frame = useRef<number | null>(null);

  useEffect(() => {
    if (deadlineAt === null) {
      setRemaining(0);
      return;
    }
    const deadline = Date.parse(deadlineAt);
    const tick = () => {
      const serverNow = Date.now() + offsetMs();
      setRemaining(Math.max(0, deadline - serverNow));
      frame.current = requestAnimationFrame(tick);
    };
    tick();
    return () => {
      if (frame.current !== null) cancelAnimationFrame(frame.current);
      frame.current = null;
    };
  }, [deadlineAt, offsetMs]);

  return { remainingMs, expired: deadlineAt !== null && remainingMs <= 0 };
}
