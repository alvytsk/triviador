"""§5.6's shutdown. The assertion that matters is the negative one: no
transaction is cancelled part-way."""

import asyncio
from dataclasses import dataclass
from datetime import timedelta

import pytest

from tests.conftest import lobby_state
from tests.runtime.conftest import (
    T0,
    GatedExecutor,
    ScriptedLoader,
    a_manager,
    manager_with_resident,
)
from tests.runtime.fakes import FakeClock, FakeSubscribers, RecordingOrigin
from triviador.domain.game.actions import JoinGame
from triviador.domain.ids import GameId, PlayerId
from triviador.runtime.errors import ServerRestarting
from triviador.runtime.manager import Recovering
from triviador.runtime.runtime import QueuedCommand
from triviador.services.ports import RuntimeCode


@dataclass
class TracingCloser:
    """Stands in for the watchdog/reaper, recording when it was closed so
    the ordering against the runtime drain is observable."""

    trace: list[str]
    label: str

    async def aclose(self) -> None:
        self.trace.append(self.label)


class SlowCloser:
    """A closer whose `aclose` genuinely suspends — unlike `TracingCloser`,
    whose body never awaits anything and so never yields control back to
    the event loop. `test_the_fence_closes_the_window_a_concurrent_get_could_slip_through`
    needs a real suspension point inside `shutdown`'s first `await` to
    give a concurrently scheduled `get()` a turn to run."""

    async def aclose(self) -> None:
        await asyncio.sleep(0)


async def test_queued_commands_are_resolved_with_server_restarting() -> None:
    """An unresolved origin is a hung HTTP request that outlives the
    process it was waiting on."""
    clock = FakeClock(T0)
    manager, runtime = manager_with_resident(lobby_state(), clock, start=False)
    first, second = RecordingOrigin(), RecordingOrigin()
    runtime.submit(QueuedCommand(JoinGame(PlayerId("p8"), "P8"), "op-1", first))
    runtime.submit(QueuedCommand(JoinGame(PlayerId("p9"), "P9"), "op-2", second))
    runtime.start()

    await manager.shutdown()

    assert first.outcome == ("failed", RuntimeCode.SERVER_RESTARTING)
    assert second.outcome == ("failed", RuntimeCode.SERVER_RESTARTING)


async def test_an_in_flight_transaction_is_allowed_to_finish() -> None:
    """Cancelling mid-COMMIT would manufacture the ambiguous-commit case
    on every deploy — the one failure mode never worth generating
    deliberately. So the consumer is never cancelled: it is asked to stop
    and finishes what it is doing first."""
    clock = FakeClock(T0)
    executor = GatedExecutor()
    manager, runtime = manager_with_resident(lobby_state(), clock, executor=executor)
    origin = RecordingOrigin()
    runtime.submit(QueuedCommand(JoinGame(PlayerId("p2"), "P2"), "op-1", origin))
    await executor.entered.wait()

    shutdown = asyncio.create_task(manager.shutdown())
    await clock.settle()
    assert not shutdown.done()  # waiting on the transaction, not cancelling it

    executor.release.set()
    await shutdown

    assert origin.outcome[0] == "ok"  # committed, not SERVER_RESTARTING
    assert PlayerId("p2") in runtime.state.players


async def test_the_watchdog_and_reaper_are_cancelled_first() -> None:
    """Before the runtimes, so neither can enqueue into a queue that is
    being drained — a race whose prize is an origin nobody resolves."""
    clock = FakeClock(T0)
    manager, runtime = manager_with_resident(lobby_state(), clock)
    trace: list[str] = []
    closer = TracingCloser(trace, "closer")
    runtime.on_drain_for_test = lambda: trace.append("drain")

    await manager.shutdown(closer)

    assert trace == ["closer", "drain"]


async def test_sockets_are_closed_with_1001() -> None:
    """1001 "going away", not 1011 "internal error": a deploy is not a
    fault, and the code is what tells the client whether to reconnect
    quietly or surface an error."""
    clock = FakeClock(T0)
    subscribers = FakeSubscribers()
    manager, runtime = manager_with_resident(lobby_state(), clock, subscribers=subscribers)

    await manager.shutdown()

    assert subscribers.closed == [(runtime.game_id, 1001)]


async def test_get_after_shutdown_raises_server_restarting() -> None:
    clock = FakeClock(T0)
    manager, runtime = manager_with_resident(lobby_state(), clock)

    await manager.shutdown()

    with pytest.raises(ServerRestarting):
        await manager.get(runtime.game_id)


async def test_shutdown_is_idempotent() -> None:
    """A lifespan handler can be invoked twice on a hard stop. The second
    call must not re-drain queues that no longer exist."""
    clock = FakeClock(T0)
    manager, _ = manager_with_resident(lobby_state(), clock)

    await manager.shutdown()
    await manager.shutdown()


async def test_a_recovering_game_cannot_install_a_runtime_after_shutdown() -> None:
    """`_recover` is an unbounded retry loop on a manager-owned task, and
    it ends by installing a fresh `Live` entry. Every await inside
    shutdown is a chance for it to do so — and a shutdown loop that only
    inspects `Live` entries would never see it."""
    clock = FakeClock(T0)
    loader = ScriptedLoader([lobby_state(), OSError("db down"), lobby_state()])
    manager = a_manager(loader, clock=clock, backoff_initial_s=1.0, backoff_max_s=8.0)
    runtime = await manager.get(GameId("g1"))
    manager.quarantine(runtime, "persistence unavailable")
    await clock.settle()
    assert isinstance(manager.entry_for(GameId("g1")), Recovering)

    await manager.shutdown()
    await clock.advance_to(T0 + timedelta(seconds=60))

    assert manager.entry_for(GameId("g1")) is None
    assert loader.calls == 2  # the initial load and the failed retry — never a third


async def test_the_fence_closes_the_window_a_concurrent_get_could_slip_through() -> None:
    """Added beyond the brief's eight: mutation-testing
    `test_a_recovering_game_cannot_install_a_runtime_after_shutdown` by
    moving the fence-set line past `shutdown`'s first `await` left that
    test passing regardless — the already-tracked `_recover` task it
    exercises is neutralised by `_cancel_lifecycle_tasks`'s cancellation,
    which happens either way, so it says nothing about *when* the fence
    itself gets set.

    The property the fence's position actually protects is a *new*
    `get()` call for a game shutdown has never touched — nothing tracks
    it, so the fence is its only defence. `SlowCloser` gives `shutdown`'s
    first `await` a genuine suspension point; a concurrently scheduled
    `get()` runs during that suspension and must see the fence already up.
    """
    clock = FakeClock(T0)
    loader = ScriptedLoader([lobby_state()])
    manager = a_manager(loader, clock=clock)

    shutdown_task = asyncio.create_task(manager.shutdown(SlowCloser()))
    get_task = asyncio.create_task(manager.get(GameId("g1")))
    results = await asyncio.gather(shutdown_task, get_task, return_exceptions=True)

    assert isinstance(results[1], ServerRestarting)


async def test_shutdown_awaits_a_quarantine_already_in_progress() -> None:
    """A quarantine task cancelled mid-teardown could leave a runtime
    detached from the registry but still consuming — invisible to the
    shutdown loop, which iterates the registry."""
    clock = FakeClock(T0)
    loader = ScriptedLoader([lobby_state(), OSError("db down")])
    manager = a_manager(loader, clock=clock)
    runtime = await manager.get(GameId("g1"))
    manager.quarantine(runtime, "boom")

    await manager.shutdown()

    assert all(task.done() for task in manager._quarantines.values())
    assert runtime.closed is True
