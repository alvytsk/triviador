"""Builders shared across the runtime suite.

`lobby_state` and friends live in `tests/conftest.py` and are reused as-is
— the runtime tests assert on runtime behaviour, not on new state shapes.
"""

from datetime import UTC, datetime

import pytest

from tests.conftest import NOW, full_pool, lobby_state
from tests.runtime.fakes import FakeBroadcaster, FakeClock, FakeSubscribers
from triviador.domain.game.actions import DecisionContext, StartGame
from triviador.domain.game.reducer import decide, fold
from triviador.domain.game.state import GameState
from triviador.domain.ids import PlayerId, RegionId

T0 = datetime(2026, 8, 17, 12, 0, tzinfo=UTC)


def _warmup_state() -> GameState:
    """A started game, parked in its MediaWarmup window.

    Shared by `test_materialiser.py` and `test_commit.py` — a plain
    function, not a fixture, because both modules also build further
    states on top of it (`test_materialiser._picking_state` drives it
    forward), which a fixture's single per-test instance would not serve.
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
