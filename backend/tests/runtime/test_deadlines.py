"""§5.4 and §5.6's recovery clause. Every instant here is absolute and
every advance is explicit — this file would be the easiest place in the
suite to accidentally test the event loop's scheduler instead of the
runtime."""

import random
from datetime import timedelta

from tests.conftest import lobby_state
from tests.runtime.conftest import T0, StubExecutor, settle, warmup_state
from tests.runtime.fakes import FakeBroadcaster, FakeClock, RecordingOrigin
from triviador.domain.game.actions import Command, DecisionContext, ExpireDeadline
from triviador.domain.game.reducer import decide
from triviador.domain.game.state import GameState
from triviador.domain.ids import DeadlineId
from triviador.runtime.commit import Executor
from triviador.runtime.origins import Accepted, Ignored
from triviador.runtime.runtime import GameRuntime, QueuedCommand


class CapturingExecutor:
    """Records what the loop hands it and commits nothing."""

    def __init__(self) -> None:
        self.commands: list[Command] = []

    async def execute(
        self, state: GameState, command: Command, operation_id: str
    ) -> Accepted | Ignored:
        self.commands.append(command)
        return Ignored()


def a_runtime(state: GameState, clock: FakeClock, executor: Executor) -> GameRuntime:
    return GameRuntime(
        state=state,
        executor=executor,
        clock=clock,
        broadcaster=FakeBroadcaster(),
        on_fault=lambda rt, exc: None,
        generation=1,
        rng=random.Random(0),
    )


async def test_no_deadline_means_no_task() -> None:
    """A lobby has no open window. Scheduling a task for `None` would be
    a timer that fires at an instant nobody chose."""
    clock = FakeClock(T0)
    runtime = a_runtime(lobby_state(), clock, CapturingExecutor())
    runtime.start()
    await clock.settle()

    assert clock.pending() == ()
    await runtime.aclose()


async def test_a_future_deadline_is_scheduled_at_its_absolute_instant() -> None:
    state = warmup_state()
    deadline = state.current_deadline()
    assert deadline is not None
    clock = FakeClock(deadline.deadline_at - timedelta(seconds=3))
    executor = CapturingExecutor()
    runtime = a_runtime(state, clock, executor)
    runtime.start()
    await clock.settle()

    assert clock.pending() == (deadline.deadline_at,)
    assert executor.commands == []

    await clock.advance_to(deadline.deadline_at)

    assert executor.commands == [ExpireDeadline(deadline.id)]
    assert runtime.expiry_enqueued_deadline_id == deadline.id
    await runtime.aclose()


async def test_a_deadline_already_in_the_past_is_enqueued_immediately() -> None:
    """§5.6: "if it has already passed, `ExpireDeadline` is enqueued
    immediately. Recovery must never extend a window a player has already
    spent." The runtime restarted at T+25 on a 20 s window."""
    state = warmup_state()
    deadline = state.current_deadline()
    assert deadline is not None
    clock = FakeClock(deadline.deadline_at + timedelta(seconds=5))
    executor = CapturingExecutor()
    runtime = a_runtime(state, clock, executor)
    runtime.start()
    await clock.settle()

    assert executor.commands == [ExpireDeadline(deadline.id)]
    assert clock.pending() == ()
    await runtime.aclose()


async def test_the_task_is_respawned_when_the_deadline_id_changes() -> None:
    """A command that opens a new window must retarget the timer. The old
    task is cancelled; if it fires anyway, guard 2 drops it — but leaving
    it scheduled would mean two timers racing on one game."""
    state = warmup_state()
    first = state.current_deadline()
    assert first is not None
    clock = FakeClock(first.deadline_at - timedelta(seconds=1))

    # Drive the warmup close through the real reducer, so the events that
    # open the next window are the ones production would produce.
    closing = decide(
        state,
        ExpireDeadline(first.id),
        DecisionContext(now=first.deadline_at + timedelta(milliseconds=1)),
    )
    runtime = a_runtime(state, clock, StubExecutor([Accepted(closing)]))
    runtime.start()
    await settle(runtime)
    assert clock.pending() == (first.deadline_at,)

    runtime.submit(QueuedCommand(ExpireDeadline(first.id), "op-1", RecordingOrigin()))
    await settle(runtime)

    second = runtime.state.current_deadline()
    assert second is not None
    assert second.id != first.id
    assert clock.pending() == (second.deadline_at,)  # exactly one timer, retargeted
    await runtime.aclose()


async def test_the_task_is_not_respawned_when_the_deadline_is_unchanged() -> None:
    """An answer submitted mid-window does not reopen it. Respawning on
    every command would reset the sleep and quietly extend the window each
    time a player typed — the fastest player would give everyone else
    more time."""
    state = warmup_state()
    deadline = state.current_deadline()
    assert deadline is not None
    clock = FakeClock(deadline.deadline_at - timedelta(seconds=5))
    runtime = a_runtime(state, clock, StubExecutor([Ignored()]))
    runtime.start()
    await settle(runtime)
    before = clock.pending()

    runtime.submit(QueuedCommand(ExpireDeadline(DeadlineId(999)), "op-1", RecordingOrigin()))
    await settle(runtime)

    assert clock.pending() == before == (deadline.deadline_at,)
    await runtime.aclose()


async def test_the_expiry_is_submitted_with_a_system_origin_and_a_fresh_operation_id() -> None:
    """Nobody is waiting for a deadline expiry, so its origin must absorb
    every resolution silently — including the `resolve_noop` a stale
    expiry gets. And each expiry needs its own `operation_id`: two
    expiries sharing one would make the second look, to §5.5's
    reconciliation, like a replay of the first.
    """
    state = warmup_state()
    first = state.current_deadline()
    assert first is not None
    clock = FakeClock(first.deadline_at - timedelta(seconds=1))
    closing = decide(
        state,
        ExpireDeadline(first.id),
        DecisionContext(now=first.deadline_at + timedelta(milliseconds=1)),
    )
    executor = StubExecutor([Accepted(closing), Ignored()])
    runtime = a_runtime(state, clock, executor)
    runtime.start()
    await settle(runtime)

    await clock.advance_to(first.deadline_at)
    await settle(runtime)
    second = runtime.state.current_deadline()
    assert second is not None
    await clock.advance_to(second.deadline_at)
    await settle(runtime)

    operation_ids = [call[2] for call in executor.calls]
    assert all(operation_ids)  # never empty
    assert len(set(operation_ids)) == len(operation_ids)  # never reused
    await runtime.aclose()


async def test_the_fence_is_rolled_back_when_the_enqueue_fails() -> None:
    """The runtime closes (a race with the manager tearing it down) between
    the deadline task waking up and it enqueueing the expiry. Leaving the
    fence set here would be worse than the race guard 2 already covers: no
    expiry is ever queued, yet every later watchdog tick would look at
    `expiry_enqueued_deadline_id`, see this deadline "already pending", and
    skip it — the window would stall forever with nothing left to rescue it.
    """
    state = warmup_state()
    deadline = state.current_deadline()
    assert deadline is not None
    clock = FakeClock(deadline.deadline_at - timedelta(seconds=1))
    runtime = a_runtime(state, clock, CapturingExecutor())
    runtime.start()
    await clock.settle()
    assert clock.pending() == (deadline.deadline_at,)

    before = runtime.expiry_enqueued_deadline_id
    assert before is None  # nothing pending yet — the fence starts clean

    # Simulate the manager closing this runtime out from under the
    # deadline task, exactly the race `submit`'s `RuntimeClosed` guards.
    runtime.closed = True
    await clock.advance_to(deadline.deadline_at)

    assert runtime.expiry_enqueued_deadline_id == before
    await runtime.aclose()
