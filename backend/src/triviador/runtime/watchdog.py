"""§11.5. One task, one tick, one job: notice a deadline that should have
fired and did not.

Every rescue it performs is a bug somewhere else — a deadline task that
was cancelled and lost its race, or one that died without firing. It is
cheap insurance against a game silently stopping, and it is written so
that being wrong costs nothing: a spurious expiry is dropped by guard 2
in `decide` (`current.id != command.deadline_id` -> zero events).
"""

import asyncio
import contextlib
import logging
from datetime import timedelta
from uuid import uuid4

from triviador.domain.game.actions import ExpireDeadline
from triviador.runtime.errors import RuntimeClosed, ServerBusy
from triviador.runtime.manager import GameManager
from triviador.runtime.origins import SystemOrigin
from triviador.runtime.runtime import QueuedCommand
from triviador.services.ports import Clock

logger = logging.getLogger(__name__)


class Watchdog:
    """Sweeps every resident runtime every `interval_s` seconds and
    rescues any deadline that is more than `grace_s` past due with no
    expiry already enqueued for it.

    `grace_s` exists so this never races the deadline task itself: firing
    the instant a deadline passes would double the command volume on
    every single window in the game, since the deadline task (Task 10) is
    also racing to enqueue at that exact instant.
    """

    def __init__(
        self,
        *,
        manager: GameManager,
        clock: Clock,
        interval_s: float = 5.0,
        grace_s: float = 5.0,
    ) -> None:
        self._manager = manager
        self._clock = clock
        self._interval_s = interval_s
        self._grace_s = grace_s
        self._task: asyncio.Task[None] | None = None

    def start(self) -> None:
        self._task = asyncio.create_task(self._run(), name="watchdog")

    async def aclose(self) -> None:
        """Task 16's shutdown cancels the watchdog first, before it tears
        down any runtime — a tick racing a runtime mid-teardown would just
        see it absent from `live_runtimes()` and skip it, but there is no
        reason to let it try."""
        if self._task is None:
            return
        self._task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await self._task
        self._task = None

    async def _run(self) -> None:
        while True:
            try:
                await self._clock.sleep_until(
                    self._clock.now() + timedelta(seconds=self._interval_s)
                )
            except asyncio.CancelledError:
                return
            self.tick()

    def tick(self) -> None:
        """Synchronous and total: it must survive any one runtime being
        closed, busy, or otherwise broken, because the other games depend
        on the next tick happening regardless of what this one does."""
        now = self._clock.now()
        for runtime in self._manager.live_runtimes():
            deadline = runtime.state.current_deadline()
            if deadline is None:
                continue
            if now <= deadline.deadline_at + timedelta(seconds=self._grace_s):
                continue
            if runtime.expiry_enqueued_deadline_id == deadline.id:
                # Fencing on *enqueued*, not on *expired*: the first
                # expiry may still be sitting in the queue behind a slow
                # command, and re-enqueuing every tick would fill the
                # queue with copies of it. The fence is per-`DeadlineId`,
                # not a latch, so a later window that stalls in turn is
                # still rescued once its own id no longer matches.
                continue

            previous_fence = runtime.expiry_enqueued_deadline_id
            runtime.expiry_enqueued_deadline_id = deadline.id
            try:
                runtime.submit(
                    QueuedCommand(
                        command=ExpireDeadline(deadline.id),
                        operation_id=f"watchdog-{runtime.game_id}-{deadline.id}-{uuid4()}",
                        origin=SystemOrigin("watchdog"),
                    )
                )
            except (RuntimeClosed, ServerBusy) as exc:
                # Roll the fence back to whatever it held before this
                # attempt — not to `None` unconditionally. A fence left
                # set after a failed enqueue is strictly worse than the
                # race it prevents: nothing is queued, and every later
                # tick now skips this deadline because the fence claims
                # an expiry is already pending. The game stalls on that
                # window forever, with the watchdog that exists to
                # rescue it looking straight past it. Task 10's
                # `_await_deadline` has the identical pattern.
                runtime.expiry_enqueued_deadline_id = previous_fence
                logger.warning(
                    "watchdog could not enqueue expiry for game %s: %s", runtime.game_id, exc
                )
            else:
                logger.warning(
                    "watchdog fired expiry for game %s deadline %s — the deadline task did not",
                    runtime.game_id,
                    deadline.id,
                )
