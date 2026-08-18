"""§12.2: drop the connection during COMMIT → reconciliation by
operation_id, no duplicate batch, no lost batch.

The whole point of these three is that the executor *cannot* tell the
cases apart from the exception it caught. It has to ask the database.
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tests.runtime.integration.conftest import (
    event_row_count,
    executor_over,
    last_seq,
    seed_user,
    submit_and_settle,
)
from triviador.db.unit_of_work import TransactionContext, UnitOfWork
from triviador.domain.game.actions import JoinGame
from triviador.domain.game.events import PlayerJoined
from triviador.domain.ids import GameId, PlayerId
from triviador.runtime.manager import GameManager, Live, Recovering
from triviador.services.ports import RuntimeCode

pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="session")]


class BreakingUnitOfWork:
    """Wraps a real `UnitOfWork` and breaks the COMMIT of its first
    transaction.

    `mode="landed"` commits and *then* raises: the write is durable and
    the caller was told it failed. `mode="lost"` rolls back and then
    raises: identical signal, opposite truth. Later transactions pass
    straight through, so the retry and the reconciliation both run
    against a healthy connection — which is what happens in production,
    where the pool hands out a new one.
    """

    def __init__(self, inner: UnitOfWork, mode: str) -> None:
        self._inner = inner
        self._mode = mode
        self._broken = False

    @asynccontextmanager
    async def begin(self) -> AsyncIterator[TransactionContext]:
        if self._broken:
            async with self._inner.begin() as tx:
                yield tx
            return

        self._broken = True
        async with self._inner._sessionmaker() as session:
            await session.begin()
            yield TransactionContext(session)
            if self._mode == "landed":
                await session.commit()
            else:
                await session.rollback()
            raise OSError("connection reset during COMMIT")


async def test_a_commit_that_lands_but_reports_failure_is_reconciled(
    manager: GameManager, lobby: GameId, sessions: async_sessionmaker[AsyncSession]
) -> None:
    """Exactly one batch in `game_events` — never two — the origin
    resolved `ok`, `last_seq` advanced once, and the runtime still Live."""
    runtime = await manager.get(lobby)
    runtime.replace_executor_for_test(
        executor_over(BreakingUnitOfWork(UnitOfWork(sessions), mode="landed"), manager)
    )

    origin = await submit_and_settle(runtime, JoinGame(PlayerId("p1"), "P1"), "join-p1")

    assert origin.outcome[0] == "ok"
    assert await event_row_count(sessions, lobby) == 2  # genesis + one join
    assert await last_seq(sessions, lobby) == 2
    assert isinstance(manager.entry_for(lobby), Live)


async def test_a_commit_that_does_not_land_is_retried(
    manager: GameManager, lobby: GameId, sessions: async_sessionmaker[AsyncSession]
) -> None:
    """Reconciliation answers ABSENT, the executor re-runs the whole
    attempt, and exactly one batch ends up committed — by the retry, not
    by the original."""
    runtime = await manager.get(lobby)
    runtime.replace_executor_for_test(
        executor_over(BreakingUnitOfWork(UnitOfWork(sessions), mode="lost"), manager)
    )

    origin = await submit_and_settle(runtime, JoinGame(PlayerId("p1"), "P1"), "join-p1")

    assert origin.outcome[0] == "ok"
    assert await event_row_count(sessions, lobby) == 2
    assert await last_seq(sessions, lobby) == 2


async def test_a_foreign_batch_under_the_same_operation_id_quarantines(
    manager: GameManager, lobby: GameId, sessions: async_sessionmaker[AsyncSession]
) -> None:
    """MISMATCH is never "close enough".

    Pre-write a *different* batch under the `operation_id` the command
    will use, then force the ambiguous path. Reconciliation finds rows
    for that operation whose ordered types are not the ones this attempt
    decided, and quarantines rather than adopting them.

    Note the pre-write also advances `last_seq`, so on some interleavings
    the attempt's own `append` raises `ConcurrentModification` first.
    Both routes quarantine, which is what is asserted — this test pins
    the outcome, and `tests/db/test_reconciliation.py` pins the
    `MISMATCH` verdict itself in isolation.

    `PlayerJoined`'s projection inserts a `game_players` row with a real
    `user_id` foreign key to `users` — a fake unit of work never enforces
    that, but real PostgreSQL does, so the foreign batch's actor has to be
    a seeded user (`p9`) rather than an arbitrary id, or this pre-write
    itself fails with `ForeignKeyViolationError` before the test ever
    reaches the ambiguous commit it exists to exercise.
    """
    runtime = await manager.get(lobby)
    await seed_user(sessions, "p9")
    async with UnitOfWork(sessions).begin() as tx:
        await tx.append(
            lobby,
            expected_last_seq=1,
            events=[PlayerJoined(PlayerId("p9"), "P9", seat=0)],
            operation_id="join-p1",
        )
    runtime.replace_executor_for_test(
        executor_over(BreakingUnitOfWork(UnitOfWork(sessions), mode="landed"), manager)
    )

    origin = await submit_and_settle(runtime, JoinGame(PlayerId("p1"), "P1"), "join-p1")

    assert origin.outcome == ("failed", RuntimeCode.GAME_RECOVERING)
    entry = manager.entry_for(lobby)
    assert isinstance(entry, Live | Recovering)
    if isinstance(entry, Live):
        assert entry.runtime.generation > runtime.generation
