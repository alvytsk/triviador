"""The one place the runtime learns what time it is."""

import asyncio
from datetime import UTC, datetime


class SystemClock:
    """`services.ports.Clock` over the real world.

    `sleep_until` clamps a past instant to zero rather than raising: on
    recovery the runtime routinely asks to sleep until a deadline that
    expired while the process was down, and §5.6 requires that case to
    resolve *immediately*, not to be an error the caller must pre-check.
    One path covers both of §5.6's cases — future instant, past instant —
    which is why neither the runtime nor the manager branches on it.
    """

    def now(self) -> datetime:
        return datetime.now(UTC)

    async def sleep_until(self, when: datetime) -> None:
        """Sleep until `when` has genuinely passed, not merely until one
        computed delay has elapsed.

        `asyncio.sleep` schedules on the loop's monotonic clock while this
        delay is computed from the wall clock, so a single sleep can return a
        few microseconds *before* `now()` reaches `when`. That is not
        harmless: the deadline task then submits an `ExpireDeadline` that
        guard 4 correctly ignores, while `expiry_enqueued_deadline_id` stays
        set — and the watchdog skips every window whose fence is already
        set, so the game stalls on that window forever.

        Looping until the instant has actually passed removes the early wake
        at its source. A past instant still resolves immediately, which is
        §5.6's recovery clause.
        """
        while True:
            delay = (when - self.now()).total_seconds()
            if delay <= 0:
                return
            await asyncio.sleep(delay)
