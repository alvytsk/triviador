"""§11.6. Two halves that pull in opposite directions: unload what nobody
needs, and never unload what somebody is playing."""

import logging
from dataclasses import replace
from datetime import timedelta

import pytest

from tests.conftest import lobby_state
from tests.runtime.conftest import (
    T0,
    GatedExecutor,
    ScriptedLoader,
    StubExecutor,
    StubGames,
    a_manager,
    a_reaper,
    manager_with_resident,
    queued_commands,
    settle,
    warmup_state,
)
from tests.runtime.fakes import FakeClock, FakeSubscribers, RecordingOrigin
from triviador.domain.game.actions import AbortGame, JoinGame
from triviador.domain.game.events import GameAborted
from triviador.domain.game.state import GameState, Phase
from triviador.domain.ids import GameId, PlayerId
from triviador.runtime.errors import PermanentReplayFailure
from triviador.runtime.manager import Failed, GameManager, Live
from triviador.runtime.origins import Accepted
from triviador.runtime.runtime import QueuedCommand


async def test_an_abandoned_lobby_found_only_in_the_database_is_aborted() -> None:
    """The named §12.2 case. The lobby was unloaded by the no-connections
    rule an hour ago, so a scan over resident runtimes would never see it
    and it would sit in the database forever."""
    clock = FakeClock(T0)
    games = StubGames(empty_lobbies=(GameId("g-old"),))
    manager = a_manager(ScriptedLoader([lobby_state(players={})]), clock=clock, games=games)
    reaper = a_reaper(manager, games, clock)

    await reaper.tick()

    entry = manager.entry_for(GameId("g-old"))
    assert isinstance(entry, Live)
    assert [qc.command for qc in queued_commands(entry.runtime)] == [AbortGame(actor_id=None)]


async def test_it_uses_the_configured_ages_for_each_query() -> None:
    """`created_before = now - 5 min` for empty lobbies, `now - 6 h` for
    stale ones. Passing the same cutoff to both would either abort every
    lobby after five minutes or leave the empty ones for six hours."""
    clock = FakeClock(T0)
    games = StubGames()
    reaper = a_reaper(a_manager(ScriptedLoader([]), clock=clock, games=games), games, clock)

    await reaper.tick()

    assert games.empty_cutoffs == [T0 - timedelta(minutes=5)]
    assert games.stale_cutoffs == [T0 - timedelta(hours=6)]


async def test_the_abort_is_system_issued_with_no_actor() -> None:
    """`AbortGame(actor_id=None)`. Guard 3 validates the actor only when
    one is present, so an actor-bearing abort would be rejected outright
    in an empty lobby — there is no participant it could name."""
    clock = FakeClock(T0)
    games = StubGames(stale_lobbies=(GameId("g-stale"),))
    manager = a_manager(ScriptedLoader([lobby_state(players={})]), clock=clock, games=games)
    reaper = a_reaper(manager, games, clock)

    await reaper.tick()

    entry = manager.entry_for(GameId("g-stale"))
    assert isinstance(entry, Live)
    command = queued_commands(entry.runtime)[0].command
    assert isinstance(command, AbortGame)
    assert command.actor_id is None


async def test_a_lobby_that_is_both_empty_and_stale_is_aborted_once() -> None:
    """Both queries can return the same row. Two aborts would mean the
    second lands on an already-aborted game — harmless, since guard 1
    drops it, but it is a command nobody needed to issue and a log line
    that reads like a bug."""
    clock = FakeClock(T0)
    games = StubGames(empty_lobbies=(GameId("g1"),), stale_lobbies=(GameId("g1"),))
    manager = a_manager(ScriptedLoader([lobby_state(players={})]), clock=clock, games=games)
    reaper = a_reaper(manager, games, clock)

    await reaper.tick()

    entry = manager.entry_for(GameId("g1"))
    assert isinstance(entry, Live)
    assert len(queued_commands(entry.runtime)) == 1


async def test_an_aborted_game_is_unloaded() -> None:
    """§11.6 treats FINISHED and ABORTED identically for unloading. This
    reaches ABORTED through `AbortGame(actor_id=None)` run through the
    runtime rather than folding a `GameFinished` onto a fully played
    game (F4): playing a game to FINISHED here would duplicate Task 17's
    integration coverage at roughly twenty folded commands of setup for a
    fact the domain already proves — that FINISHED and ABORTED are both
    terminal."""
    clock = FakeClock(T0)
    executor = StubExecutor([Accepted((GameAborted("aborted by system"),))])
    manager, runtime = manager_with_resident(lobby_state(), clock, executor=executor)
    runtime.submit(QueuedCommand(AbortGame(actor_id=None), "op-1", RecordingOrigin()))
    await settle(runtime)
    assert runtime.state.phase is Phase.ABORTED
    reaper = a_reaper(manager, StubGames(), clock)

    await reaper.tick()

    assert manager.entry_for(runtime.game_id) is None


async def test_a_lobby_with_no_connections_is_unloaded() -> None:
    clock = FakeClock(T0)
    manager, runtime = manager_with_resident(lobby_state(), clock)
    reaper = a_reaper(manager, StubGames(), clock, subscribers=FakeSubscribers())

    await reaper.tick()

    assert manager.entry_for(runtime.game_id) is None


async def test_a_lobby_with_a_connection_is_kept() -> None:
    clock = FakeClock(T0)
    manager, runtime = manager_with_resident(lobby_state(), clock)
    subscribers = FakeSubscribers({runtime.game_id: 1})
    reaper = a_reaper(manager, StubGames(), clock, subscribers=subscribers)

    await reaper.tick()

    assert isinstance(manager.entry_for(runtime.game_id), Live)


async def test_an_active_game_is_never_unloaded_even_with_zero_connections() -> None:
    """§11.6 is explicit: EXPANSION / BATTLE, never unload, regardless of
    presence. Unloading one would orphan its DeadlineId and the game would
    stop advancing while looking healthy — and §12.2's presence case says
    disconnecting the last player must not pause the game."""
    clock = FakeClock(T0)
    manager, runtime = manager_with_resident(warmup_state(), clock)
    assert runtime.state.phase is Phase.EXPANSION
    reaper = a_reaper(manager, StubGames(), clock, subscribers=FakeSubscribers())

    await reaper.tick()

    assert isinstance(manager.entry_for(runtime.game_id), Live)


async def test_a_runtime_with_queued_work_is_not_unloaded() -> None:
    """Unloading is not a fault, so it must not resolve anybody's origin
    with a failure code. If there is queued work, skip this tick."""
    clock = FakeClock(T0)
    manager, runtime = manager_with_resident(lobby_state(), clock, start=False)
    origin = RecordingOrigin()
    runtime.submit(QueuedCommand(JoinGame(PlayerId("p9"), "P9"), "op-1", origin))
    reaper = a_reaper(manager, StubGames(), clock, subscribers=FakeSubscribers())

    await reaper.tick()

    assert isinstance(manager.entry_for(runtime.game_id), Live)
    assert origin.resolutions == []


async def test_a_runtime_executing_a_command_is_not_unloaded() -> None:
    """The race an empty queue hides. `_consume` dequeues *before* it
    executes, so for the whole duration of the transaction — append,
    COMMIT — `qsize()` reads zero while a command is very much in
    progress. Unloading on that reading cancels the consumer mid-COMMIT:
    the ambiguous-commit case, manufactured deliberately, plus an origin
    nobody ever resolves."""
    clock = FakeClock(T0)
    executor = GatedExecutor()
    manager, runtime = manager_with_resident(lobby_state(), clock, executor=executor)
    origin = RecordingOrigin()
    runtime.submit(QueuedCommand(JoinGame(PlayerId("p2"), "P2"), "op-1", origin))
    await executor.entered.wait()
    assert runtime.pending_commands() == 0  # the lie is_idle() exists to correct
    reaper = a_reaper(manager, StubGames(), clock, subscribers=FakeSubscribers())

    await reaper.tick()

    assert isinstance(manager.entry_for(runtime.game_id), Live)

    executor.release.set()
    await settle(runtime)
    assert origin.outcome[0] == "ok"


async def test_an_unload_that_finds_the_runtime_busy_leaves_it_submittable() -> None:
    """`unload` sets `closed` before checking, so a submit racing it fails
    loudly rather than landing in a queue about to be discarded. But when
    the check then says "busy", `closed` must be rolled back — otherwise a
    game nobody unloaded is left permanently refusing commands, and only a
    re-`get()` no caller knows to make would revive it."""
    clock = FakeClock(T0)
    manager, runtime = manager_with_resident(lobby_state(), clock, start=False)
    runtime.submit(QueuedCommand(JoinGame(PlayerId("p9"), "P9"), "op-1", RecordingOrigin()))

    unloaded = await manager.unload(runtime.game_id)

    assert unloaded is False
    assert runtime.closed is False
    runtime.submit(QueuedCommand(JoinGame(PlayerId("p8"), "P8"), "op-2", RecordingOrigin()))


async def test_an_unload_that_finds_the_runtime_busy_restores_a_prior_closed_state() -> None:
    """The rollback must restore whatever `closed` held *before* this
    call, not a literal `False`. `GameManager.shutdown` (Task 16) can set
    `closed = True` for its own reasons and then race a reaper tick's
    `unload` — rolling back to a literal `False` would reopen the "no
    submit succeeds anywhere" window shutdown had just closed, until
    shutdown's own runtime teardown re-closes it."""
    clock = FakeClock(T0)
    manager, runtime = manager_with_resident(lobby_state(), clock, start=False)
    runtime.submit(QueuedCommand(JoinGame(PlayerId("p9"), "P9"), "op-1", RecordingOrigin()))
    runtime.closed = True  # e.g. already marked closed by a concurrent shutdown

    unloaded = await manager.unload(runtime.game_id)

    assert unloaded is False
    assert runtime.closed is True


async def test_one_failing_game_does_not_stop_the_sweep() -> None:
    """A lobby that will not load is the manager's problem — it has
    already been recorded `Failed` or `Recovering`. The other nineteen
    abandoned lobbies still need aborting."""
    clock = FakeClock(T0)
    games = StubGames(empty_lobbies=(GameId("g-bad"), GameId("g-good")))
    manager = a_manager(
        ScriptedLoader([PermanentReplayFailure("bad digest"), lobby_state(players={})]),
        clock=clock,
        games=games,
    )
    reaper = a_reaper(manager, games, clock)

    await reaper.tick()

    assert isinstance(manager.entry_for(GameId("g-bad")), Failed)
    entry = manager.entry_for(GameId("g-good"))
    assert isinstance(entry, Live)
    assert [qc.command for qc in queued_commands(entry.runtime)] == [AbortGame(actor_id=None)]


class _FlipsShuttingDownMidLoad:
    """Loads the first game normally; loading it flips the manager's
    `_shutting_down` flag as a side effect, standing in for a SIGTERM
    landing mid-sweep. By the time the loop reaches the next `game_id`,
    `_load`'s own fence is already up and raises `ServerRestarting` for
    every one of them — the same technique
    `test_recover_active_games_aborts_if_shutdown_races_the_sweep` in
    `test_manager.py` uses against `recover_active_games`, which is why
    this case was invisible there too until that test existed.
    """

    def __init__(self) -> None:
        self.manager: GameManager | None = None

    async def load(self, game_id: GameId) -> GameState:
        assert self.manager is not None
        self.manager._shutting_down = True
        return replace(lobby_state(), game_id=game_id)


async def test_a_shutdown_mid_sweep_is_not_logged_as_per_game_failures(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """`ServerRestarting` is a fence condition, not a per-game failure: it
    says nothing about `g2` or `g3` in particular, only that the process
    is exiting — and once `_shutting_down` flips, every remaining
    `game_id` in the batch raises the identical exception. Folding it into
    the generic per-game handler would turn one ordinary shutdown into N
    misleading ERROR-level "could not load lobby" tracebacks, one per
    lobby the sweep never got to look at."""
    clock = FakeClock(T0)
    loader = _FlipsShuttingDownMidLoad()
    games = StubGames(empty_lobbies=(GameId("g1"), GameId("g2"), GameId("g3")))
    manager = a_manager(loader, clock=clock, games=games)
    loader.manager = manager
    reaper = a_reaper(manager, games, clock)

    with caplog.at_level(logging.ERROR):
        await reaper.tick()

    assert "could not load lobby" not in caplog.text
    assert isinstance(manager.entry_for(GameId("g1")), Live)
    assert manager.entry_for(GameId("g2")) is None
    assert manager.entry_for(GameId("g3")) is None
