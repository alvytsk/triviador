"""Builders shared across the runtime suite.

`lobby_state` and friends live in `tests/conftest.py` and are reused as-is
— the runtime tests assert on runtime behaviour, not on new state shapes.
"""

import asyncio
from datetime import UTC, datetime

import pytest

from tests.conftest import NOW, full_pool, lobby_state
from tests.runtime.fakes import FakeBroadcaster, FakeClock, FakeSubscribers
from triviador.domain.game.actions import Command, DecisionContext, StartGame
from triviador.domain.game.events import PlayerJoined
from triviador.domain.game.reducer import decide, fold
from triviador.domain.game.state import GameState
from triviador.domain.ids import PlayerId, RegionId
from triviador.runtime.origins import Accepted, Ignored, Rejected
from triviador.runtime.runtime import GameRuntime

T0 = datetime(2026, 8, 17, 12, 0, tzinfo=UTC)


def warmup_state() -> GameState:
    """A started game, parked in its MediaWarmup window.

    Shared by `test_materialiser.py`, `test_commit.py` and
    `test_deadlines.py` — a plain function, not a fixture, because
    several modules also build further states on top of it
    (`test_materialiser._picking_state` drives it forward), which a
    fixture's single per-test instance would not serve.
    """
    state = lobby_state()
    ctx = DecisionContext(
        now=NOW,
        shuffled_player_ids=tuple(state.players),
        base_regions=(RegionId("r0"), RegionId("r2"), RegionId("r6")),
        drawn_pool=full_pool(),
    )
    return fold(state, decide(state, StartGame(PlayerId("p1")), ctx))


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock(T0)


@pytest.fixture
def broadcaster() -> FakeBroadcaster:
    return FakeBroadcaster()


@pytest.fixture
def subscribers() -> FakeSubscribers:
    return FakeSubscribers()


class StubExecutor:
    """Returns scripted outcomes. The executor's own behaviour is Task 8's
    subject; here it is a boundary.

    Annotated to satisfy `runtime.commit.Executor` structurally — never by
    subclassing it. A stub that drifts from the Protocol then fails
    `mypy --strict` at the call site, which is where it is cheapest to
    notice. Shared by Tasks 9, 12, 14 and 15.
    """

    def __init__(self, outcomes: list[Accepted | Ignored | Rejected | Exception]) -> None:
        self._outcomes = outcomes
        self.calls: list[tuple[int, Command, str]] = []

    async def execute(
        self, state: GameState, command: Command, operation_id: str
    ) -> Accepted | Ignored | Rejected:
        self.calls.append((state.seq, command, operation_id))
        outcome = self._outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


class GatedExecutor:
    """Blocks inside `execute` until released — stands in for a COMMIT in
    flight. Shared with `test_shutdown.py` and `test_reaper.py`."""

    def __init__(self) -> None:
        self.entered = asyncio.Event()
        self.release = asyncio.Event()

    async def execute(
        self, state: GameState, command: Command, operation_id: str
    ) -> Accepted | Ignored | Rejected:
        self.entered.set()
        await self.release.wait()
        return Accepted((PlayerJoined(PlayerId("p2"), "P2", seat=1),))


async def drain_runtime(runtime: GameRuntime, *, max_turns: int = 200) -> None:
    """Advances the fake clock until the runtime goes idle, or raises.

    `FakeClock.settle()`'s fixed three yields were tuned for Task 2's own
    tests. This is the first suite driving a full consumer chain (submit
    -> dequeue -> execute -> fold -> publish -> resolve), which can need
    more scheduling turns than that — and the right number is not
    something a test should have to predict. Looping until `is_idle()`
    replaces the guess with an observation; `max_turns` keeps a genuinely
    stuck consumer a fast failure rather than a hang.
    """
    clock = runtime.clock
    assert isinstance(clock, FakeClock)
    for _ in range(max_turns):
        await clock.settle()
        if runtime.is_idle():
            return
    raise AssertionError(f"game {runtime.game_id} never went idle")


async def settle(runtime: GameRuntime) -> None:
    """`drain_runtime` under the name `test_deadlines.py` uses.

    A deadline task registering its wait with the clock is exactly the
    kind of extra scheduling hop `drain_runtime`'s loop-until-idle exists
    for: a fixed number of `clock.settle()` yields would be a guess at
    how many hops a given command chain needs, which is the guess the
    no-wall-clock rule exists to avoid.
    """
    await drain_runtime(runtime)
