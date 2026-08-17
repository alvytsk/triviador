"""§5.2/§5.3: the materialiser runs inside the command's transaction and
resolves every non-deterministic input into a `DecisionContext`, so
`decide` stays a mathematical function and replay never diverges."""

import random
from collections.abc import Sequence
from datetime import timedelta

import pytest

from tests.conftest import full_pool, lobby_state
from tests.runtime.conftest import T0, warmup_state
from tests.runtime.fakes import FakeClock
from triviador.db.errors import InsufficientQuestions, MalformedQuestion
from triviador.domain.game.actions import (
    DecisionContext,
    ExpireDeadline,
    JoinGame,
    RejectCode,
    RejectedCommand,
    StartGame,
)
from triviador.domain.game.events import GameEvent
from triviador.domain.game.reducer import decide, fold
from triviador.domain.game.rules import required_question_budget
from triviador.domain.game.state import ExpansionPicking, GameState
from triviador.domain.ids import DeadlineId, GameId, PlayerId, QuestionId
from triviador.domain.questions.types import QuestionBudget, QuestionKind, QuestionPool
from triviador.runtime.materialiser import Materialiser
from triviador.services.ports import EventRef, QuestionPoolUnavailable, ReconcileOutcome


class StubBank:
    def __init__(self, pool: QuestionPool | None = None, raises: Exception | None = None) -> None:
        self._pool = pool if pool is not None else full_pool()
        self._raises = raises
        self.budgets: list[QuestionBudget] = []

    async def select_pool(self, budget: QuestionBudget) -> QuestionPool:
        self.budgets.append(budget)
        if self._raises is not None:
            raise self._raises
        return self._pool


class StubTransaction:
    """Only `questions` is exercised here — the materialiser never appends,
    loads a stream, looks up an operation, or reconciles one. Those four
    methods are still part of the Protocol this class must satisfy
    (`services.ports.Transaction`), so they raise rather than being
    silently absent, which is what would let `Materialiser.build` narrow
    its parameter type away from the full port without a test noticing."""

    def __init__(self, bank: StubBank) -> None:
        self._bank = bank

    @property
    def questions(self) -> StubBank:
        return self._bank

    async def append(
        self,
        game_id: GameId,
        *,
        expected_last_seq: int,
        events: Sequence[GameEvent],
        operation_id: str,
    ) -> None:
        raise AssertionError("not reached in materialiser tests")

    async def load_stream(self, game_id: GameId) -> tuple[GameEvent, ...]:
        raise AssertionError("not reached in materialiser tests")

    async def events_for_operation(
        self, game_id: GameId, operation_id: str
    ) -> tuple[EventRef, ...]:
        raise AssertionError("not reached in materialiser tests")

    async def operation_matches(
        self,
        game_id: GameId,
        operation_id: str,
        *,
        expected_base_seq: int,
        events: Sequence[GameEvent],
    ) -> ReconcileOutcome:
        raise AssertionError("not reached in materialiser tests")


async def test_now_comes_from_the_clock_for_every_command() -> None:
    clock = FakeClock(T0)
    materialiser = Materialiser(clock=clock, rng=random.Random(0))
    tx = StubTransaction(StubBank())

    ctx = await materialiser.build(lobby_state(), JoinGame(PlayerId("p9"), "P9"), tx)

    assert ctx.now == T0
    assert ctx.drawn_pool is None
    assert ctx.shuffled_player_ids is None
    assert ctx.base_regions is None
    assert ctx.shuffled_region_ids is None


async def test_start_game_draws_the_pool_for_the_rules_budget() -> None:
    bank = StubBank()
    materialiser = Materialiser(clock=FakeClock(T0), rng=random.Random(0))
    state = lobby_state()

    ctx = await materialiser.build(state, StartGame(PlayerId("p1")), StubTransaction(bank))

    assert bank.budgets == [required_question_budget(state.rules)]
    assert ctx.drawn_pool is not None


async def test_start_game_context_satisfies_decide() -> None:
    """The real gate: `_decide_start` rejects an incomplete context, so a
    context this method builds must survive it and produce events."""
    materialiser = Materialiser(clock=FakeClock(T0), rng=random.Random(7))
    state = lobby_state()

    ctx = await materialiser.build(state, StartGame(PlayerId("p1")), StubTransaction(StubBank()))
    events = decide(state, StartGame(PlayerId("p1")), ctx)

    assert events
    assert ctx.shuffled_player_ids is not None
    assert set(ctx.shuffled_player_ids) == set(state.players)


async def test_start_game_bases_are_mutually_non_adjacent() -> None:
    """Spec 1 §3.4, enforced here because `_decide_start` cannot: it
    validates distinctness and membership, never adjacency."""
    materialiser = Materialiser(clock=FakeClock(T0), rng=random.Random(3))
    state = lobby_state()

    ctx = await materialiser.build(state, StartGame(PlayerId("p1")), StubTransaction(StubBank()))

    assert ctx.base_regions is not None
    for region in ctx.base_regions:
        assert state.map.neighbours(region).isdisjoint(ctx.base_regions)


@pytest.mark.parametrize(
    "error",
    [
        InsufficientQuestions(kind=QuestionKind.NUMERIC, required=17, available=2),
        MalformedQuestion(question_id=QuestionId("q1"), kind=QuestionKind.NUMERIC),
    ],
    ids=["insufficient", "malformed"],
)
async def test_a_bank_shortfall_leaves_the_pool_none_and_becomes_a_rejection(
    error: QuestionPoolUnavailable,
) -> None:
    """§5.5: an insufficient bank is an ordinary rejection, not a fault —
    and a malformed row is treated the same, because quarantining a
    healthy lobby over one bad content row turns a fixable data problem
    into a reload loop. The policy is stated once, in `decide`."""
    materialiser = Materialiser(clock=FakeClock(T0), rng=random.Random(0))
    state = lobby_state()

    ctx = await materialiser.build(
        state, StartGame(PlayerId("p1")), StubTransaction(StubBank(raises=error))
    )

    assert ctx.drawn_pool is None
    with pytest.raises(RejectedCommand) as caught:
        decide(state, StartGame(PlayerId("p1")), ctx)
    assert caught.value.code is RejectCode.QUESTION_POOL_INSUFFICIENT


async def test_a_database_failure_in_the_bank_propagates() -> None:
    """Not every bank failure is a rejection. §5.5: an exception in the
    materialiser from the *database* quarantines — only a domain shortfall
    is a rejection, so this one must not be swallowed."""
    materialiser = Materialiser(clock=FakeClock(T0), rng=random.Random(0))

    with pytest.raises(RuntimeError):
        await materialiser.build(
            lobby_state(),
            StartGame(PlayerId("p1")),
            StubTransaction(StubBank(raises=RuntimeError("conn lost"))),
        )


async def test_expire_deadline_during_picking_shuffles_free_regions() -> None:
    """`_decide_auto_pick` falls back to `state.free_regions()` when
    `shuffled_region_ids` is None — map order, every time, for every
    timed-out pick in every game. The shuffle is what makes an auto-pick
    an arbitrary free region rather than always the lowest-numbered one."""
    materialiser = Materialiser(clock=FakeClock(T0), rng=random.Random(11))
    state = _picking_state()
    assert isinstance(state.turn, ExpansionPicking)

    ctx = await materialiser.build(
        state, ExpireDeadline(state.turn.deadline.id), StubTransaction(StubBank())
    )

    assert ctx.shuffled_region_ids is not None
    assert set(ctx.shuffled_region_ids) == set(state.free_regions())


async def test_expire_deadline_outside_picking_leaves_region_order_none() -> None:
    """Materialise what this command needs, nothing else: a shuffle no
    reducer branch reads is dead weight in every answer window."""
    materialiser = Materialiser(clock=FakeClock(T0), rng=random.Random(0))
    state = warmup_state()

    ctx = await materialiser.build(
        state, ExpireDeadline(DeadlineId(1)), StubTransaction(StubBank())
    )

    assert ctx.shuffled_region_ids is None


def _picking_state() -> GameState:
    """Drive the warmup and the first expansion question to a timeout, which
    lands on ExpansionPicking with grants to hand out."""
    from tests.conftest import expire_warmup

    state = expire_warmup(warmup_state())
    assert state.turn is not None
    state = fold(
        state,
        decide(
            state,
            ExpireDeadline(state.turn.deadline.id),
            DecisionContext(now=state.turn.deadline.deadline_at + timedelta(seconds=1)),
        ),
    )
    if not isinstance(state.turn, ExpansionPicking):
        assert state.turn is not None
        state = fold(
            state,
            decide(
                state,
                ExpireDeadline(state.turn.deadline.id),
                DecisionContext(now=state.turn.deadline.deadline_at + timedelta(seconds=1)),
            ),
        )
    assert isinstance(state.turn, ExpansionPicking)
    return state
