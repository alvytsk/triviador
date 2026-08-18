"""§5.6. The hard part is not the teardown — it is that recovery can fail
too, and that commands queued against R17 must never surface in R18."""

import random
from datetime import timedelta

import pytest

from tests.conftest import lobby_state
from tests.runtime.conftest import T0, ScriptedLoader, StubExecutor, a_manager
from tests.runtime.fakes import FakeClock, FakeSubscribers, RecordingOrigin
from triviador.domain.game.actions import JoinGame
from triviador.domain.ids import GameId, PlayerId
from triviador.runtime.errors import (
    CommitFault,
    GameRecovering,
    PermanentReplayFailure,
    RuntimeClosed,
)
from triviador.runtime.manager import Failed, Live, Recovering
from triviador.runtime.runtime import QueuedCommand
from triviador.services.ports import RuntimeCode

GAME = GameId("g1")


class MaxJitter(random.Random):
    """`uniform(a, b)` always returns `b`.

    `test_the_backoff_grows_and_is_capped` needs the *sampled* delay to
    reveal exactly the upper bound `_recover` passed to `uniform` —
    deterministically, on any seed. Asserting against the suite's default
    `random.Random(0)` let "drop the `min(...)` cap, keep the jitter"
    through undetected: with that fixed seed, all four sampled delays
    happened to land under the cap regardless of whether the cap was
    actually applied, so the mutation passed on every run. A stub that
    always samples the top of its range removes that dependency on luck:
    if the cap is missing, the fourth attempt's upper bound genuinely is
    8.0, and the sampled delay is genuinely 8.0.

    Subclasses `random.Random` rather than a bare duck-typed object:
    `GameManager.__init__`'s `rng` parameter is typed against the concrete
    class, not a Protocol, so `mypy --strict` only accepts an actual
    subtype here.
    """

    def uniform(self, a: float, b: float) -> float:
        return b


async def test_a_fault_tears_down_and_loads_a_fresh_generation() -> None:
    clock = FakeClock(T0)
    loader = ScriptedLoader([lobby_state(), lobby_state()])
    manager = a_manager(loader, clock=clock)
    old = await manager.get(GAME)

    manager.quarantine(old, "boom")
    await clock.settle()

    entry = manager.entry_for(GAME)
    assert isinstance(entry, Live)
    assert entry.runtime is not old
    assert entry.runtime.generation > old.generation
    assert old.closed is True
    assert loader.calls == 2


async def test_quarantine_does_not_run_on_the_faulting_task() -> None:
    """A task cannot cancel and await itself. If teardown ran inline, the
    consumer task would be awaiting its own cancellation and hang here
    forever — so the assertion is simply that it finished."""
    clock = FakeClock(T0)
    manager = a_manager(ScriptedLoader([lobby_state(), lobby_state()]), clock=clock)
    old = await manager.get(GAME)
    old.replace_executor_for_test(StubExecutor([CommitFault("boom")]))

    old.submit(QueuedCommand(JoinGame(PlayerId("p2"), "P2"), "op-1", RecordingOrigin()))
    await clock.settle()

    assert old.consumer_done()
    assert isinstance(manager.entry_for(GAME), Live)


async def test_queued_commands_are_resolved_with_game_recovering() -> None:
    """Anything still in the queue when a runtime is torn down will never
    be processed. An unresolved origin is a request that hangs until its
    client gives up."""
    clock = FakeClock(T0)
    manager = a_manager(ScriptedLoader([lobby_state(), lobby_state()]), clock=clock)
    old = await manager.get(GAME)
    first, second = RecordingOrigin(), RecordingOrigin()
    old.submit(QueuedCommand(JoinGame(PlayerId("p8"), "P8"), "op-1", first))
    old.submit(QueuedCommand(JoinGame(PlayerId("p9"), "P9"), "op-2", second))

    manager.quarantine(old, "boom")
    await clock.settle()

    assert first.outcome == ("failed", RuntimeCode.GAME_RECOVERING)
    assert second.outcome == ("failed", RuntimeCode.GAME_RECOVERING)


async def test_nothing_queued_against_the_old_generation_reaches_the_new_one() -> None:
    """§12.2's generation quarantine, stated as a test. A command that
    survived into the replacement runtime would be decided against a
    state it never saw."""
    clock = FakeClock(T0)
    manager = a_manager(ScriptedLoader([lobby_state(), lobby_state()]), clock=clock)
    old = await manager.get(GAME)
    origins = [RecordingOrigin(), RecordingOrigin()]
    for i, origin in enumerate(origins):
        old.submit(QueuedCommand(JoinGame(PlayerId(f"p{i}"), f"P{i}"), f"op-{i}", origin))

    manager.quarantine(old, "boom")
    await clock.settle()

    entry = manager.entry_for(GAME)
    assert isinstance(entry, Live)
    new = entry.runtime
    assert new.generation > old.generation
    assert new.pending_commands() == 0
    assert all(o.outcome == ("failed", RuntimeCode.GAME_RECOVERING) for o in origins)
    with pytest.raises(RuntimeClosed):
        old.submit(QueuedCommand(JoinGame(PlayerId("px"), "PX"), "op-x", RecordingOrigin()))


async def test_subscribers_are_closed_with_1011_through_the_port() -> None:
    """1011 "internal error", and through the port: the sockets stay owned
    by the hub, which is the only thing that knows how to close one."""
    clock = FakeClock(T0)
    subscribers = FakeSubscribers()
    manager = a_manager(
        ScriptedLoader([lobby_state(), lobby_state()]), clock=clock, subscribers=subscribers
    )
    old = await manager.get(GAME)

    manager.quarantine(old, "boom")
    await clock.settle()

    assert subscribers.closed == [(GAME, 1011)]


async def test_a_transient_recovery_failure_stays_recovering_and_retries() -> None:
    """§5.6: quarantine is reached *because* something broke — most often
    persistence — so "immediately load a fresh generation" is the least
    likely operation to succeed at that moment."""
    clock = FakeClock(T0)
    loader = ScriptedLoader([lobby_state(), OSError("db down"), lobby_state()])
    manager = a_manager(loader, clock=clock, backoff_initial_s=1.0, backoff_max_s=8.0)
    runtime = await manager.get(GAME)

    manager.quarantine(runtime, "persistence unavailable")
    await clock.settle()

    assert isinstance(manager.entry_for(GAME), Recovering)
    with pytest.raises(GameRecovering):
        await manager.get(GAME)

    await clock.advance_to(T0 + timedelta(seconds=8))

    assert isinstance(manager.entry_for(GAME), Live)
    assert loader.calls == 3


async def test_the_backoff_grows_and_is_capped() -> None:
    """Assert the *bound*, never an exact delay: the backoff is jittered,
    and an exact-value assertion here would only restate the
    implementation — and would fail the day someone changes the jitter
    without changing the behaviour that matters.

    `rng=MaxJitter()` rather than the suite's default seeded
    `random.Random(0)`: this test's whole point is that the *sampled*
    delay never exceeds the cap, and with the default seed every sampled
    delay happened to land under the cap even with the cap deleted from
    production — a deterministic blind spot, not a flaky one. `MaxJitter`
    always samples the top of whatever range `_recover` passes to
    `uniform`, so the sampled delay exactly exposes that upper bound.
    """
    clock = FakeClock(T0)
    loader = ScriptedLoader([lobby_state(), *[OSError("db down")] * 4, lobby_state()])
    manager = a_manager(
        loader, clock=clock, backoff_initial_s=1.0, backoff_max_s=4.0, rng=MaxJitter()
    )
    runtime = await manager.get(GAME)

    manager.quarantine(runtime, "persistence unavailable")
    await clock.settle()

    waits: list[float] = []
    for attempt in range(1, 5):
        pending = clock.pending()
        assert len(pending) == 1, "exactly one recovery timer at a time"
        delay = (pending[0] - clock.now()).total_seconds()
        assert 0.0 <= delay <= min(4.0, 1.0 * 2 ** (attempt - 1))
        waits.append(delay)
        await clock.advance_to(pending[0])

    assert isinstance(manager.entry_for(GAME), Live)
    # The cap is what is asserted, not monotonicity: full jitter means any
    # single delay can be small. The ceiling is what stops a long outage
    # turning into an ever-growing wait.
    assert all(w <= 4.0 for w in waits)


async def test_a_permanent_recovery_failure_goes_to_failed_without_retrying() -> None:
    """No backoff, no second attempt: replay will never succeed, and
    retrying only hides the incident."""
    clock = FakeClock(T0)
    loader = ScriptedLoader([lobby_state(), PermanentReplayFailure("bad digest")])
    manager = a_manager(loader, clock=clock)
    runtime = await manager.get(GAME)

    manager.quarantine(runtime, "boom")
    await clock.settle()

    assert isinstance(manager.entry_for(GAME), Failed)
    assert clock.pending() == ()  # nothing scheduled: it is not coming back
    assert loader.calls == 2
    assert manager.degraded() == ((GAME, "bad digest"),)


async def test_a_second_fault_from_the_same_runtime_is_ignored() -> None:
    """The consumer stops after reporting, but the deadline task or a
    caller may report again. A second teardown would destroy the
    replacement generation the first one just installed."""
    clock = FakeClock(T0)
    loader = ScriptedLoader([lobby_state(), lobby_state()])
    manager = a_manager(loader, clock=clock)
    old = await manager.get(GAME)

    manager.quarantine(old, "boom")
    manager.quarantine(old, "boom again")
    await clock.settle()

    assert loader.calls == 2  # one reload, not two
    entry = manager.entry_for(GAME)
    assert isinstance(entry, Live)
    assert entry.runtime.generation == old.generation + 1
