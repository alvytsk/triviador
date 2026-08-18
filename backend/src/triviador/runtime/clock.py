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
        delay = (when - self.now()).total_seconds()
        await asyncio.sleep(max(0.0, delay))
