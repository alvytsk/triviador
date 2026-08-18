"""One command, one transaction — and everything §5.5 says to do when that
transaction does not go to plan.

The shape is §5.2's consumer loop with the loop and the origins removed:

    async with uow.begin() as tx:
        ctx    = await materialiser.build(state, command, tx)
        events = decide(state, command, ctx)
        await tx.append(...)
    # COMMIT — every lock released here

Isolating it buys two things. The consumer loop (Task 9) stays a loop you
can read in one screen, and every branch of the failure table can be
tested against a fake unit of work with no queue, no task, and no clock
advancing anywhere.
"""

import logging
import random
from collections.abc import Sequence
from datetime import timedelta
from typing import Protocol

from triviador.domain.game.actions import Command, RejectedCommand
from triviador.domain.game.events import GameEvent
from triviador.domain.game.reducer import decide
from triviador.domain.game.state import GameState
from triviador.runtime.errors import CommitFault
from triviador.runtime.materialiser import Materialiser
from triviador.runtime.origins import Accepted, Ignored, Rejected
from triviador.services.ports import Clock, ReconcileOutcome, UnitOfWorkPort

logger = logging.getLogger(__name__)

# §5.5's "known rollback": serialization failure and deadlock detected.
# Both mean the transaction definitively did not commit, which is what
# makes re-running the whole attempt safe.
RETRYABLE_SQLSTATES = frozenset({"40001", "40P01"})


class Executor(Protocol):
    """What Task 9's consumer loop asks of a command executor.

    Declared here, structurally, so a test stub satisfies it without
    subclassing `CommandExecutor` — the same reason `services.ports`
    declares its Protocols rather than a real class.
    """

    async def execute(
        self, state: GameState, command: Command, operation_id: str
    ) -> Accepted | Ignored | Rejected: ...


class _NoEvents(Exception):
    """Internal: forces a rollback for a no-op.

    Exiting the `async with` normally would COMMIT an empty transaction —
    harmless, but it holds the connection for a round trip and reads as if
    something was written. §5.5 says rollback; this makes it literal.
    """


def _sqlstate(exc: BaseException) -> str | None:
    """SQLAlchemy wraps the driver error and exposes it as `.orig`;
    asyncpg's `PostgresError` carries `.sqlstate`. Reaching through both
    with `getattr` rather than importing either keeps `runtime/` free of
    the driver — the alternative is a `db` import in the one module that
    most needs to stay portable."""
    orig = getattr(exc, "orig", None)
    sqlstate = getattr(orig, "sqlstate", None)
    return sqlstate if isinstance(sqlstate, str) else None


class CommandExecutor:
    def __init__(
        self,
        *,
        uow: UnitOfWorkPort,
        materialiser: Materialiser,
        clock: Clock,
        rng: random.Random,
        max_attempts: int = 3,
        backoff_base_s: float = 0.05,
    ) -> None:
        self._uow = uow
        self._materialiser = materialiser
        self._clock = clock
        self._rng = rng
        self._max_attempts = max_attempts
        self._backoff_base_s = backoff_base_s

    async def execute(
        self, state: GameState, command: Command, operation_id: str
    ) -> Accepted | Ignored | Rejected:
        for attempt in range(1, self._max_attempts + 1):
            events: tuple[GameEvent, ...] = ()
            appended = False
            try:
                async with self._uow.begin() as tx:
                    ctx = await self._materialiser.build(state, command, tx)
                    events = decide(state, command, ctx)
                    if not events:
                        raise _NoEvents
                    await tx.append(
                        state.game_id,
                        expected_last_seq=state.seq,
                        events=events,
                        operation_id=operation_id,
                    )
                    appended = True
            except _NoEvents:
                return Ignored()
            except RejectedCommand as exc:
                # Raised out of the `async with`, so the transaction rolled
                # back on the way past — including any FOR SHARE locks a
                # StartGame draw had taken. §5.5: state untouched, runtime
                # healthy, reply to the origin.
                return Rejected(exc.code, exc.message)
            except Exception as exc:
                # SQLSTATE first, *before* the ambiguity check — order is
                # load-bearing. A serialization failure or deadlock is
                # reported by PostgreSQL at COMMIT as often as before it,
                # so it routinely arrives with `appended` already true.
                # But it is not ambiguous: 40001 and 40P01 mean the
                # transaction definitively did not commit. Checking
                # `appended` first would send every one of them through a
                # reconciliation round trip that can only ever answer
                # ABSENT — an extra transaction, on the exact path that is
                # already under contention.
                if _sqlstate(exc) in RETRYABLE_SQLSTATES:
                    logger.warning(
                        "game %s: retryable rollback on attempt %d/%d",
                        state.game_id,
                        attempt,
                        self._max_attempts,
                    )
                    await self._backoff(attempt)
                    continue

                if appended:
                    # `append` returned and this is not a known rollback,
                    # so the only operation left was the COMMIT and
                    # whether it landed is unknown. This is the ambiguous
                    # commit, and the *only* path that may look for rows
                    # written by a previous attempt.
                    outcome = await self._reconcile(state, operation_id, events)
                    if outcome is not None:
                        return outcome
                    continue  # ABSENT: nothing landed, re-run the attempt

                raise CommitFault(f"game {state.game_id}: command attempt failed") from exc

            return Accepted(events)

        raise CommitFault(
            f"game {state.game_id}: persistence unavailable after {self._max_attempts} attempts"
        )

    async def _reconcile(
        self, state: GameState, operation_id: str, events: Sequence[GameEvent]
    ) -> Accepted | None:
        """Returns `Accepted` if the batch committed, `None` if it
        definitively did not (the caller re-runs), and raises `CommitFault`
        on a mismatch or if the reconciliation itself cannot run.

        A fresh unit of work: the previous one's connection is exactly
        what failed.
        """
        try:
            async with self._uow.begin() as tx:
                verdict = await tx.operation_matches(
                    state.game_id,
                    operation_id,
                    expected_base_seq=state.seq,
                    events=events,
                )
        except Exception as exc:
            raise CommitFault(
                f"game {state.game_id}: cannot reconcile operation {operation_id}"
            ) from exc

        match verdict:
            case ReconcileOutcome.MATCHED:
                logger.warning(
                    "game %s: ambiguous commit for %s resolved as committed",
                    state.game_id,
                    operation_id,
                )
                return Accepted(tuple(events))
            case ReconcileOutcome.ABSENT:
                logger.warning(
                    "game %s: ambiguous commit for %s did not land; retrying",
                    state.game_id,
                    operation_id,
                )
                return None
            case ReconcileOutcome.MISMATCH:
                raise CommitFault(
                    f"game {state.game_id}: operation {operation_id} committed a batch "
                    f"that is not the one this attempt decided"
                )

    async def _backoff(self, attempt: int) -> None:
        """Full jitter, through the clock rather than `asyncio.sleep`, so
        the fake clock governs it and no test waits on wall-clock time.
        With `backoff_base_s=0` this is a no-op — which is how most tests
        run it."""
        if self._backoff_base_s <= 0:
            return
        delay = self._rng.uniform(0.0, self._backoff_base_s * (2 ** (attempt - 1)))
        await self._clock.sleep_until(self._clock.now() + timedelta(seconds=delay))
