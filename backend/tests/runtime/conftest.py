"""Builders shared across the runtime suite.

`lobby_state` and friends live in `tests/conftest.py` and are reused as-is
— the runtime tests assert on runtime behaviour, not on new state shapes.
"""

import asyncio
import random
from collections.abc import AsyncIterator
from contextlib import AbstractAsyncContextManager
from dataclasses import replace
from datetime import UTC, datetime

import pytest

from tests.conftest import NOW, full_pool, lobby_state
from tests.runtime.fakes import FakeBroadcaster, FakeClock, FakeSubscribers
from triviador.domain.game.actions import Command, DecisionContext, StartGame
from triviador.domain.game.events import PlayerJoined
from triviador.domain.game.reducer import decide, fold
from triviador.domain.game.state import GameState
from triviador.domain.ids import GameId, PlayerId, RegionId
from triviador.runtime.manager import GameManager, Loader
from triviador.runtime.materialiser import Materialiser
from triviador.runtime.origins import Accepted, Ignored, Rejected
from triviador.runtime.runtime import GameRuntime
from triviador.services.ports import Transaction

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


class ScriptedLoader:
    """`outcomes` is consumed one per load: a `GameState`, or an exception
    to raise.

    Shared by Tasks 12, 13 and 15 — Task 12's quarantine/recovery tests
    and Task 13's startup recovery both need a loader that returns a
    different, pre-scripted outcome on each call, which `CountingLoader`
    (always the same lobby, or always the same exception) cannot do.
    """

    def __init__(self, outcomes: list[GameState | Exception]) -> None:
        self._outcomes = outcomes
        self.calls = 0

    async def load(self, game_id: GameId) -> GameState:
        self.calls += 1
        outcome = self._outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


class CountingLoader:
    """Counts calls and awaits once, standing in for `runtime.loader.GameLoader`.

    The `asyncio.sleep(0)` is not decoration: `GameManager.get`'s
    concurrency test only exercises the per-game lock if the loader
    genuinely yields control mid-load — a loader that ran to completion
    without ever awaiting would let every one of eight concurrent `get`
    calls finish in one uninterrupted step each, serializing them by
    accident and passing the test even with the lock deleted. A real
    loader awaits the database, so this is the honest shape.

    Returns `lobby_state()` stamped with the requested `game_id`, not the
    hardcoded `GameId("g1")` `lobby_state()` defaults to: every prior use
    only ever loaded one game per test, so the mismatch never showed.
    Task 13's `recover_active_games` is the first caller to load two
    games in the same test — without the `replace`, both runtimes would
    carry `state.game_id == "g1"` and `live_runtimes()` would report one
    game twice instead of two.

    Shared by Tasks 11, 12, 13 and 15.
    """

    def __init__(self, raises: Exception | None = None) -> None:
        self.calls = 0
        self._raises = raises

    async def load(self, game_id: GameId) -> GameState:
        self.calls += 1
        if self._raises is not None:
            raise self._raises
        await asyncio.sleep(0)  # a real load awaits the database
        return replace(lobby_state(), game_id=game_id)


class _NoUnitOfWork:
    """`GameManager`'s registry tests never open a transaction — `begin`
    raises rather than being silently absent, the same reason
    `StubUnitOfWork` in `test_loader.py` raises on the methods its own
    tests never reach. `object()` would fail `mypy --strict` against the
    port-typed parameter and say nothing about why; this documents the
    boundary."""

    def begin(self) -> AbstractAsyncContextManager[Transaction]:
        raise AssertionError("not reached in registry tests")


class _NoMaterialiser(Materialiser):
    """Same boundary as `_NoUnitOfWork`, for the collaborator `_load`
    wires into `CommandExecutor` but never calls in these tests.

    Subclasses `Materialiser` rather than standing alone: it is a
    concrete dataclass, not a Protocol, so `GameManager.__init__`'s
    `materialiser` parameter (and `CommandExecutor`'s, one hop further
    in) can only accept a real subtype — a structurally-similar stub
    would not satisfy `mypy --strict` here the way it does for the
    Protocol-typed ports.
    """

    async def build(self, state: GameState, command: Command, tx: Transaction) -> DecisionContext:
        raise AssertionError("not reached in registry tests")


class _NoGameQueries:
    """Same boundary as `_NoUnitOfWork`, for `GameQueriesPort` — unused
    until Task 15's reaper walks it."""

    async def find_empty_lobbies(self, *, created_before: datetime) -> tuple[GameId, ...]:
        raise AssertionError("not reached in registry tests")

    async def find_stale_lobbies(self, *, created_before: datetime) -> tuple[GameId, ...]:
        raise AssertionError("not reached in registry tests")

    async def find_unfinished(self) -> tuple[GameId, ...]:
        raise AssertionError("not reached in registry tests")


class StubGames:
    """`GameQueriesPort`, answered from a fixed script rather than a
    database.

    Task 13 only needs `find_unfinished`; Task 15's reaper extends this
    same class with `empty_lobbies`/`stale_lobbies` scripting and cutoff
    recording, so the other two methods are here now, returning `()`,
    rather than each task growing its own stub.
    """

    def __init__(self, unfinished: tuple[GameId, ...] = ()) -> None:
        self.unfinished = unfinished

    async def find_unfinished(self) -> tuple[GameId, ...]:
        return self.unfinished

    async def find_empty_lobbies(self, *, created_before: datetime) -> tuple[GameId, ...]:
        return ()

    async def find_stale_lobbies(self, *, created_before: datetime) -> tuple[GameId, ...]:
        return ()


_created_managers: list[GameManager] = []


def a_manager(loader: Loader, **overrides: object) -> GameManager:
    """Builds a `GameManager` wired for the registry suite: real fakes for
    the collaborators `get` actually touches (clock, broadcaster,
    subscribers), raising stubs for the ones it never reaches (uow,
    materialiser, games). Tasks 12, 13, 14 and 15 all call this, several
    of them through `clock=`, `subscribers=`, `games=`, `backoff_initial_s=`
    and `backoff_max_s=` overrides.

    Defaults live in a dict merged with `overrides` rather than as keyword
    arguments on the `GameManager(...)` call itself: passing `clock=` (or
    any other) both positionally-as-keyword and via `**overrides` would be
    a `TypeError` — "got multiple values for keyword argument" — the
    moment a test actually used the override this docstring promises.

    Registered into `_created_managers` so `_close_started_runtimes` below
    can find and close whatever `GameRuntime`s this manager started, the
    same obligation `test_deadlines.py` and `test_runtime_loop.py` meet
    with an explicit `await runtime.aclose()` in every test — `get()`
    calls `runtime.start()`, which spawns a consumer task, and a test that
    never submits anything leaves that task parked on `await
    self._queue.get()` forever if nobody closes it.
    """
    kwargs: dict[str, object] = dict(
        loader=loader,
        uow=_NoUnitOfWork(),
        materialiser=_NoMaterialiser(clock=FakeClock(T0), rng=random.Random(0)),
        clock=FakeClock(T0),
        broadcaster=FakeBroadcaster(),
        subscribers=FakeSubscribers(),
        games=_NoGameQueries(),
        rng=random.Random(0),
    )
    kwargs.update(overrides)
    manager = GameManager(**kwargs)  # type: ignore[arg-type]
    _created_managers.append(manager)
    return manager


@pytest.fixture(autouse=True)
async def _close_started_runtimes() -> AsyncIterator[None]:
    """Closes every runtime a `a_manager`-built manager still holds live.

    Central rather than per-test so Tasks 12-15 — which all reuse
    `a_manager` — inherit the cleanup instead of each having to remember
    `await runtime.aclose()`. `test_get_reloads_a_closed_runtime` closes
    its *first* runtime itself as part of the scenario it is testing;
    that runtime is no longer in `live_runtimes()` by teardown time
    (`_entries` was overwritten on reload), so this only ever reaches the
    still-open second one — nothing here is a double close.

    A no-op for every other module in this directory: `_created_managers`
    stays empty unless a test called `a_manager`.
    """
    yield
    managers, _created_managers[:] = list(_created_managers), []
    for manager in managers:
        for runtime in manager.live_runtimes():
            if not runtime.closed:
                await runtime.aclose()
