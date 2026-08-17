"""§5.5's failure table, one test per row."""

import random
from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager

import pytest

from tests.conftest import full_pool, lobby_state
from tests.runtime.conftest import T0, _warmup_state
from tests.runtime.fakes import FakeClock
from triviador.domain.game.actions import JoinGame, StartGame
from triviador.domain.game.events import GameEvent, PlayerJoined
from triviador.domain.ids import GameId, PlayerId
from triviador.domain.questions.types import QuestionBudget, QuestionPool
from triviador.runtime.commit import CommandExecutor
from triviador.runtime.errors import CommitFault
from triviador.runtime.materialiser import Materialiser
from triviador.runtime.origins import Accepted, Ignored, Rejected
from triviador.services.ports import EventRef, ReconcileOutcome


class FakeSerializationFailure(Exception):
    """Stands in for a wrapped asyncpg error. The executor classifies on
    the SQLSTATE it can reach through `.orig.sqlstate`, exactly as
    SQLAlchemy exposes it, so the fake carries the same shape."""

    def __init__(self, sqlstate: str) -> None:
        super().__init__(sqlstate)
        self.orig = type("Orig", (), {"sqlstate": sqlstate})()


class FakeBank:
    def __init__(self, pool: QuestionPool | None = None) -> None:
        self._pool = pool if pool is not None else full_pool()
        self.draws = 0

    async def select_pool(self, budget: QuestionBudget) -> QuestionPool:
        self.draws += 1
        return self._pool


class FakeTransaction:
    def __init__(self, uow: "FakeUnitOfWork") -> None:
        self._uow = uow

    @property
    def questions(self) -> FakeBank:
        return self._uow.bank

    async def append(
        self,
        game_id: GameId,
        *,
        expected_last_seq: int,
        events: Sequence[GameEvent],
        operation_id: str,
    ) -> None:
        self._uow.appends.append((expected_last_seq, tuple(events), operation_id))
        if self._uow.append_raises:
            raise self._uow.append_raises.pop(0)

    async def load_stream(self, game_id: GameId) -> tuple[GameEvent, ...]:
        raise AssertionError("the executor never replays")

    async def events_for_operation(
        self, game_id: GameId, operation_id: str
    ) -> tuple[EventRef, ...]:
        raise AssertionError("the executor reconciles through operation_matches")

    async def operation_matches(
        self,
        game_id: GameId,
        operation_id: str,
        *,
        expected_base_seq: int,
        events: Sequence[GameEvent],
    ) -> ReconcileOutcome:
        self._uow.reconciliations += 1
        return self._uow.reconcile_verdict


class FakeUnitOfWork:
    """`exit_raises` is Spec 1 §12.2's "break the commit": the failure
    arrives when the context manager exits, which is where a real COMMIT
    fails."""

    def __init__(self) -> None:
        self.bank = FakeBank()
        self.appends: list[tuple[int, tuple[GameEvent, ...], str]] = []
        self.append_raises: list[Exception] = []
        self.exit_raises: list[Exception] = []
        self.begins = 0
        self.reconciliations = 0
        self.reconcile_verdict = ReconcileOutcome.ABSENT

    @asynccontextmanager
    async def begin(self) -> AsyncIterator[FakeTransaction]:
        self.begins += 1
        yield FakeTransaction(self)
        if self.exit_raises:
            raise self.exit_raises.pop(0)


def executor(uow: FakeUnitOfWork, *, max_attempts: int = 3) -> CommandExecutor:
    clock = FakeClock(T0)
    return CommandExecutor(
        uow=uow,
        materialiser=Materialiser(clock=clock, rng=random.Random(0)),
        clock=clock,
        rng=random.Random(0),
        max_attempts=max_attempts,
        backoff_base_s=0.0,
    )


async def test_a_decided_batch_is_appended_and_accepted() -> None:
    uow = FakeUnitOfWork()
    state = lobby_state(players={"p1": 0})

    outcome = await executor(uow).execute(state, JoinGame(PlayerId("p2"), "P2"), "op-1")

    assert isinstance(outcome, Accepted)
    assert [type(e) for e in outcome.events] == [PlayerJoined]
    assert uow.appends[0][0] == state.seq
    assert uow.appends[0][2] == "op-1"


async def test_zero_events_roll_back_and_return_ignored() -> None:
    """§5.2: a no-op resolves before ever reaching `append` — no evolve,
    no reschedule, no publish, and nothing written."""
    uow = FakeUnitOfWork()
    state = _warmup_state()
    from triviador.domain.game.actions import ExpireDeadline
    from triviador.domain.ids import DeadlineId

    outcome = await executor(uow).execute(state, ExpireDeadline(DeadlineId(999)), "op-1")

    assert isinstance(outcome, Ignored)
    assert uow.appends == []


async def test_a_rejected_command_rolls_back_and_reports_the_code() -> None:
    uow = FakeUnitOfWork()
    state = lobby_state(players={"p1": 0, "p2": 1, "p3": 2})

    outcome = await executor(uow).execute(state, JoinGame(PlayerId("p4"), "P4"), "op-1")

    assert isinstance(outcome, Rejected)
    assert outcome.code.value == "game_full"
    assert uow.appends == []


@pytest.mark.parametrize("sqlstate", ["40001", "40P01"])
async def test_a_known_rollback_retries_the_whole_attempt(sqlstate: str) -> None:
    """Not just the append: the materialiser runs again too. The FOR SHARE
    locks were released at rollback, so a reused pool would be appended
    under locks that no longer hold.

    Note where the fake raises: on *exit*, i.e. at COMMIT, with `append`
    already returned. That is the common case in production and it is the
    reason SQLSTATE must be classified before the ambiguity check — the
    `reconciliations == 0` assertion below is what pins that ordering.
    """
    uow = FakeUnitOfWork()
    uow.exit_raises = [FakeSerializationFailure(sqlstate)]
    state = lobby_state()

    outcome = await executor(uow).execute(state, StartGame(PlayerId("p1")), "op-1")

    assert isinstance(outcome, Accepted)
    assert uow.begins == 2
    assert uow.bank.draws == 2  # re-materialised, not reused
    assert uow.reconciliations == 0  # a known rollback is never ambiguous


async def test_retries_are_bounded_and_then_fault() -> None:
    uow = FakeUnitOfWork()
    uow.exit_raises = [FakeSerializationFailure("40001") for _ in range(3)]

    with pytest.raises(CommitFault):
        await executor(uow, max_attempts=3).execute(
            lobby_state(players={"p1": 0}), JoinGame(PlayerId("p2"), "P2"), "op-1"
        )
    assert uow.begins == 3


async def test_a_failure_after_append_reconciles_and_accepts_a_matching_batch() -> None:
    """Spec 1 §12.2's ambiguous commit: drop the connection during COMMIT
    → reconciliation by operation_id, no duplicate batch, no lost batch."""
    uow = FakeUnitOfWork()
    uow.exit_raises = [OSError("connection reset")]
    uow.reconcile_verdict = ReconcileOutcome.MATCHED

    outcome = await executor(uow).execute(
        lobby_state(players={"p1": 0}), JoinGame(PlayerId("p2"), "P2"), "op-1"
    )

    assert isinstance(outcome, Accepted)
    assert uow.reconciliations == 1
    assert len(uow.appends) == 1  # never appended twice


async def test_an_absent_batch_after_an_ambiguous_commit_is_retried() -> None:
    """Nothing landed, so re-running is safe — and better than
    quarantining a game over a connection that dropped for free."""
    uow = FakeUnitOfWork()
    uow.exit_raises = [OSError("connection reset")]
    uow.reconcile_verdict = ReconcileOutcome.ABSENT

    outcome = await executor(uow).execute(
        lobby_state(players={"p1": 0}), JoinGame(PlayerId("p2"), "P2"), "op-1"
    )

    assert isinstance(outcome, Accepted)
    assert uow.begins == 3  # attempt, reconcile, retry


async def test_a_mismatched_batch_faults() -> None:
    """Any mismatch is quarantine, never close enough."""
    uow = FakeUnitOfWork()
    uow.exit_raises = [OSError("connection reset")]
    uow.reconcile_verdict = ReconcileOutcome.MISMATCH

    with pytest.raises(CommitFault):
        await executor(uow).execute(
            lobby_state(players={"p1": 0}), JoinGame(PlayerId("p2"), "P2"), "op-1"
        )


async def test_an_unclassified_failure_before_append_faults_without_retrying() -> None:
    """`ConcurrentModification` arrives this way: raised inside `append`
    before it returns, with no SQLSTATE. Retrying it would append events
    decided against state that is no longer current — ADR-002's divergence
    failure, made durable."""
    uow = FakeUnitOfWork()
    uow.append_raises = [RuntimeError("concurrent modification")]

    with pytest.raises(CommitFault):
        await executor(uow).execute(
            lobby_state(players={"p1": 0}), JoinGame(PlayerId("p2"), "P2"), "op-1"
        )
    assert uow.begins == 1
    assert uow.reconciliations == 0


async def test_a_reconciliation_that_cannot_run_faults() -> None:
    uow = FakeUnitOfWork()
    uow.exit_raises = [OSError("connection reset"), OSError("still down")]

    with pytest.raises(CommitFault):
        await executor(uow).execute(
            lobby_state(players={"p1": 0}), JoinGame(PlayerId("p2"), "P2"), "op-1"
        )
