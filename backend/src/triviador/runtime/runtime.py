"""One game, one queue, one consumer task.

Single-threaded by construction: every mutation of `self._state` happens
in `_consume`, so there is no lock on the state and no window where a
half-applied batch is visible. Everything else on this class either feeds
the queue or reads the current state.
"""

import asyncio
import contextlib
import logging
import random
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime
from uuid import uuid4

from triviador.domain.game.actions import AbortGame, Command, ExpireDeadline
from triviador.domain.game.events import GameEvent
from triviador.domain.game.reducer import fold
from triviador.domain.game.state import GameState
from triviador.domain.ids import DeadlineId, GameId
from triviador.runtime.commit import Executor
from triviador.runtime.errors import CommitFault, RuntimeClosed, ServerBusy
from triviador.runtime.origins import Accepted, Ignored, Rejected, SystemOrigin
from triviador.services.ports import Broadcaster, Clock, Origin, RuntimeCode

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class QueuedCommand:
    command: Command
    operation_id: str
    origin: Origin
    stop: bool = False

    @classmethod
    def stop_sentinel(cls) -> "QueuedCommand":
        """Ends the consumer loop cleanly (Task 16).

        A typed field rather than a sentinel object smuggled through
        `command`: `mypy --strict` would reject the latter, and rightly —
        the loop checks `stop` before it ever looks at `command`.
        """
        return cls(
            command=AbortGame(actor_id=None),
            operation_id="",
            origin=SystemOrigin("shutdown"),
            stop=True,
        )


class GameRuntime:
    def __init__(
        self,
        *,
        state: GameState,
        executor: Executor,
        clock: Clock,
        broadcaster: Broadcaster,
        on_fault: Callable[["GameRuntime", BaseException], None],
        generation: int,
        rng: random.Random,
        queue_maxsize: int = 256,
    ) -> None:
        self._state = state
        self._executor = executor
        self._clock = clock
        self._broadcaster = broadcaster
        self._on_fault = on_fault
        self.generation = generation
        self._rng = rng
        self._queue: asyncio.Queue[QueuedCommand] = asyncio.Queue(maxsize=queue_maxsize)
        self._consumer: asyncio.Task[None] | None = None
        self._deadline_task: asyncio.Task[None] | None = None
        self._scheduled_deadline_id: DeadlineId | None = None
        self.expiry_enqueued_deadline_id: DeadlineId | None = None
        self._in_flight = False
        self.closed = False

    @property
    def game_id(self) -> GameId:
        return self._state.game_id

    @property
    def state(self) -> GameState:
        return self._state

    @property
    def clock(self) -> Clock:
        """Read-only, and exposed only so tests can drive the fake. The
        runtime itself always goes through `self._clock`."""
        return self._clock

    def pending_commands(self) -> int:
        return self._queue.qsize()

    def is_idle(self) -> bool:
        """Nothing queued *and* nothing in flight.

        `qsize() == 0` alone is a lie: `_consume` removes a command from
        the queue before executing it, so throughout the entire
        transaction — the append, the COMMIT — the queue reads empty
        while a command is very much in progress. A caller that unloaded
        on that reading would cancel the consumer mid-COMMIT, which is
        both the ambiguous-commit case the design goes out of its way
        never to manufacture and an origin nobody ever resolves.

        `_in_flight` is set and cleared by the consumer itself, so it
        cannot disagree with what the consumer is actually doing.
        """
        return self._queue.empty() and not self._in_flight

    def consumer_done(self) -> bool:
        """True once the consumer task has actually finished — not merely
        asked to stop. Exists so a test can assert the loop stopped
        without reaching into `_consumer`."""
        return self._consumer is not None and self._consumer.done()

    def start(self) -> None:
        self._consumer = asyncio.create_task(
            self._consume(), name=f"consume:{self.game_id}:{self.generation}"
        )
        self._reschedule_deadline()

    def submit(self, qc: QueuedCommand) -> None:
        """Synchronous, and it raises rather than resolving.

        An origin belongs to the caller until this returns successfully;
        from that instant it belongs to the runtime, which resolves it
        exactly once. Resolving here would be a double resolution the
        moment the caller also handled the raise.

        `ServerBusy` rather than blocking: the caller is a WebSocket read
        loop that must not stall (§5.6).
        """
        if self.closed:
            raise RuntimeClosed(f"game {self.game_id} generation {self.generation} is closed")
        try:
            self._queue.put_nowait(qc)
        except asyncio.QueueFull as exc:
            raise ServerBusy(f"game {self.game_id} command queue is full") from exc

    def drain(self, code: RuntimeCode, message: str) -> int:
        """Resolve and discard everything queued. Used by quarantine
        (`GAME_RECOVERING`) and shutdown (`SERVER_RESTARTING`) — the two
        places where queued commands will never be processed and their
        origins would otherwise hang forever."""
        drained = 0
        while True:
            try:
                qc = self._queue.get_nowait()
            except asyncio.QueueEmpty:
                return drained
            qc.origin.resolve_failed(code, message)
            drained += 1

    async def _consume(self) -> None:
        while True:
            qc = await self._queue.get()
            if qc.stop:
                return
            # Set *before* the try and cleared in `finally`: the window
            # this closes is the one between dequeuing a command and
            # finishing it, during which `qsize()` reads zero and the
            # reaper would otherwise judge this runtime idle and cancel
            # it mid-transaction.
            self._in_flight = True
            try:
                await self._apply(qc)
            except CommitFault as exc:
                # Resolve *this* origin before handing off: quarantine
                # drains the queue, and a command already dequeued is not
                # in it. Then report to the manager and stop consuming —
                # teardown must not run on this task, because a task
                # cannot cancel and await itself (§5.6).
                qc.origin.resolve_failed(RuntimeCode.GAME_RECOVERING, str(exc))
                logger.error("game %s: quarantining — %s", self.game_id, exc)
                self._on_fault(self, exc)
                return
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # pragma: no cover — belt and braces
                qc.origin.resolve_failed(RuntimeCode.GAME_RECOVERING, str(exc))
                logger.exception("game %s: unexpected consumer failure", self.game_id)
                self._on_fault(self, exc)
                return
            finally:
                self._in_flight = False

    async def _apply(self, qc: QueuedCommand) -> None:
        base_seq = self._state.seq
        outcome = await self._executor.execute(self._state, qc.command, qc.operation_id)
        # Past this line every database lock is released: `execute` opened
        # and closed its own transaction. §5.2 — no external response is
        # produced while locks are held.

        match outcome:
            case Ignored():
                qc.origin.resolve_noop()
                return
            case Rejected(code=code, message=message):
                qc.origin.resolve_rejected(code, message)
                return
            case Accepted(events=events):
                self._state = fold(self._state, events)
                self._reschedule_deadline()
                self._publish(base_seq, events)
                qc.origin.resolve_ok(events)

    def _publish(self, base_seq: int, events: Sequence[GameEvent]) -> None:
        """§5.5: a broadcaster failure is logged and never quarantines.
        The commit is durable and memory is correct; §8.5 already gives
        every client an unconditional recovery path."""
        try:
            self._broadcaster.publish(self.game_id, base_seq, self._state, events)
        except Exception:
            logger.exception("game %s: broadcast failed after commit", self.game_id)

    def _reschedule_deadline(self) -> None:
        """One-shot task, cancelled and respawned whenever
        `current_deadline().id` changes (§5.4).

        Keyed on the id, not on the instant: two different windows can
        share a `deadline_at` down to the microsecond, and re-arming on
        every command would reset the sleep each time a player answered —
        quietly extending the window they are racing.
        """
        deadline = self._state.current_deadline()
        target = deadline.id if deadline is not None else None
        if target == self._scheduled_deadline_id:
            return

        if self._deadline_task is not None:
            self._deadline_task.cancel()
            self._deadline_task = None
        self._scheduled_deadline_id = target

        if deadline is None:
            return

        self._deadline_task = asyncio.create_task(
            self._await_deadline(deadline.id, deadline.deadline_at),
            name=f"deadline:{self.game_id}:{deadline.id}",
        )

    async def _await_deadline(self, deadline_id: DeadlineId, when: datetime) -> None:
        """Sleeps until an absolute instant and submits one expiry.

        `sleep_until` on a past instant returns immediately, which is
        exactly §5.6's recovery clause — a window that expired while the
        process was down is expired *now*, not restarted. One code path
        covers both of that clause's cases.

        A stale fire is harmless under guard 2 (`current.id !=
        command.deadline_id` → zero events), so correctness never depends
        on cancellation winning the race against a wake-up.
        """
        try:
            await self._clock.sleep_until(when)
        except asyncio.CancelledError:
            return

        previous_fence = self.expiry_enqueued_deadline_id
        self.expiry_enqueued_deadline_id = deadline_id
        try:
            self.submit(
                QueuedCommand(
                    command=ExpireDeadline(deadline_id),
                    operation_id=f"deadline-{self.game_id}-{deadline_id}-{uuid4()}",
                    origin=SystemOrigin("deadline"),
                )
            )
        except (RuntimeClosed, ServerBusy):
            # The fence must be *rolled back* when the enqueue fails.
            # Setting it first closes the window in which a watchdog tick
            # sees a queued expiry with no fence and enqueues a second —
            # but leaving it set after a failure is far worse: nothing is
            # in the queue, and every later tick now skips this deadline
            # because the fence says an expiry is already pending. The
            # game would stall on that window forever, with the watchdog
            # that exists to rescue it looking straight past it.
            #
            # Closed: the manager is tearing this runtime down and the new
            # generation will re-arm from the rebuilt state. Busy: 256
            # commands are queued, and the watchdog must stay free to
            # re-fire once the queue drains.
            self.expiry_enqueued_deadline_id = previous_fence
            logger.warning("game %s: could not enqueue expiry for %s", self.game_id, deadline_id)

    async def stop(self) -> None:
        """End the consumer loop *without* cancelling it.

        The sentinel goes in after `closed` is set, so nothing can be
        submitted behind it, and the consumer picks it up only once it has
        finished whatever it was doing. That is the whole point:
        cancelling a consumer mid-COMMIT would manufacture the
        ambiguous-commit case — on every deploy for shutdown (Task 16),
        and on an unlucky tick for the reaper (Task 15).

        The deadline task *is* cancelled: it holds no transaction, and a
        timer firing into a queue nobody will read again is noise.
        """
        self.closed = True
        if self._deadline_task is not None:
            self._deadline_task.cancel()
        if self._consumer is not None:
            self._queue.put_nowait(QueuedCommand.stop_sentinel())
            await self._consumer
            self._consumer = None
        if self._deadline_task is not None:
            with contextlib.suppress(asyncio.CancelledError):
                await self._deadline_task
            self._deadline_task = None

    async def aclose(self) -> None:
        """The ungraceful counterpart, for quarantine only.

        Quarantine is reached because something already broke, and the
        in-flight transaction is usually the thing that broke — waiting
        for it politely could mean waiting on a dead connection's
        timeout. Everywhere else, use `stop()`.
        """
        self.closed = True
        for task in (self._deadline_task, self._consumer):
            if task is not None:
                task.cancel()
        for task in (self._deadline_task, self._consumer):
            if task is not None:
                with contextlib.suppress(asyncio.CancelledError):
                    await task
        self._deadline_task = None
        self._consumer = None
