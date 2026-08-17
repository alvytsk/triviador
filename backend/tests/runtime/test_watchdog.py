"""§11.5. The watchdog exists for the case where the deadline task died
without firing — a cancelled task that lost its race, an exception nobody
saw. It must fix that case and no other."""

from dataclasses import replace
from datetime import timedelta

from tests.conftest import lobby_state
from tests.runtime.conftest import (
    T0,
    drain_queue,
    manager_holding,
    queued_commands,
    stalled_runtime,
    warmup_state,
)
from tests.runtime.fakes import FakeClock, RecordingOrigin
from triviador.domain.game.actions import DecisionContext, ExpireDeadline
from triviador.domain.game.reducer import decide, fold
from triviador.domain.ids import DeadlineId, GameId
from triviador.runtime.runtime import QueuedCommand
from triviador.runtime.watchdog import Watchdog


async def test_it_enqueues_an_expiry_for_a_deadline_past_its_grace() -> None:
    state = warmup_state()
    deadline = state.current_deadline()
    assert deadline is not None
    clock = FakeClock(deadline.deadline_at + timedelta(seconds=6))
    runtime = stalled_runtime(state, clock)
    watchdog = Watchdog(manager=manager_holding(runtime), clock=clock, grace_s=5.0)

    watchdog.tick()

    assert [qc.command for qc in queued_commands(runtime)] == [ExpireDeadline(deadline.id)]
    assert runtime.expiry_enqueued_deadline_id == deadline.id


async def test_it_leaves_a_deadline_inside_its_grace_alone() -> None:
    """`now > deadline_at + grace`, not `now > deadline_at`. Firing at the
    instant the deadline passes would race the deadline task on every
    single window in the game and double the command volume."""
    state = warmup_state()
    deadline = state.current_deadline()
    assert deadline is not None
    clock = FakeClock(deadline.deadline_at + timedelta(seconds=4))
    runtime = stalled_runtime(state, clock)
    watchdog = Watchdog(manager=manager_holding(runtime), clock=clock, grace_s=5.0)

    watchdog.tick()

    assert queued_commands(runtime) == []
    assert runtime.expiry_enqueued_deadline_id is None


async def test_it_does_not_double_enqueue_while_an_expiry_is_queued() -> None:
    """The named failure: fencing on "last expired" instead of "last
    enqueued" means every tick re-enqueues while the first expiry waits
    in the queue — a briefly stalled consumer would wake to 256 copies of
    one command."""
    state = warmup_state()
    deadline = state.current_deadline()
    assert deadline is not None
    clock = FakeClock(deadline.deadline_at + timedelta(seconds=6))
    runtime = stalled_runtime(state, clock)
    watchdog = Watchdog(manager=manager_holding(runtime), clock=clock, grace_s=5.0)

    watchdog.tick()
    await clock.advance_to(clock.now() + timedelta(seconds=5))
    watchdog.tick()
    await clock.advance_to(clock.now() + timedelta(seconds=5))
    watchdog.tick()

    assert [qc.command for qc in queued_commands(runtime)] == [ExpireDeadline(deadline.id)]


async def test_it_enqueues_again_once_the_deadline_id_changes() -> None:
    """The fence is per `DeadlineId`, not a latch. A new window that also
    stalls must still be rescued — otherwise the watchdog protects
    exactly one window per game, forever."""
    state = warmup_state()
    first = state.current_deadline()
    assert first is not None
    clock = FakeClock(first.deadline_at + timedelta(seconds=6))
    runtime = stalled_runtime(state, clock)
    watchdog = Watchdog(manager=manager_holding(runtime), clock=clock, grace_s=5.0)

    watchdog.tick()
    drain_queue(runtime)

    # Advance the game to a new window, exactly as a committed command
    # would: fold the events that close the warmup.
    closing = decide(
        state,
        ExpireDeadline(first.id),
        DecisionContext(now=first.deadline_at + timedelta(milliseconds=1)),
    )
    runtime.replace_state_for_test(fold(state, closing))
    second = runtime.state.current_deadline()
    assert second is not None
    await clock.advance_to(second.deadline_at + timedelta(seconds=6))

    watchdog.tick()

    assert [qc.command for qc in queued_commands(runtime)] == [ExpireDeadline(second.id)]


async def test_it_ignores_a_runtime_with_no_open_deadline() -> None:
    """A lobby has no window. `current_deadline()` is None and there is
    nothing to rescue."""
    clock = FakeClock(T0)
    runtime = stalled_runtime(lobby_state(), clock)
    watchdog = Watchdog(manager=manager_holding(runtime), clock=clock, grace_s=5.0)

    watchdog.tick()

    assert queued_commands(runtime) == []


async def test_a_full_queue_or_closed_runtime_does_not_kill_the_tick() -> None:
    """One sick game must not stop the watchdog from rescuing the other
    nineteen. `tick` is total by construction."""
    state = warmup_state()
    deadline = state.current_deadline()
    assert deadline is not None
    clock = FakeClock(deadline.deadline_at + timedelta(seconds=6))

    # `manager_holding` keys its registry by `game_id`, and every one of
    # these would share `state.game_id` untouched — give each a distinct
    # id or two of the three collapse into one registry entry.
    full = stalled_runtime(replace(state, game_id=GameId("g-full")), clock, queue_maxsize=1)
    full.submit(QueuedCommand(ExpireDeadline(DeadlineId(1)), "filler", RecordingOrigin()))
    closed = stalled_runtime(replace(state, game_id=GameId("g-closed")), clock)
    closed.closed = True
    healthy = stalled_runtime(replace(state, game_id=GameId("g-healthy")), clock)

    watchdog = Watchdog(manager=manager_holding(full, closed, healthy), clock=clock, grace_s=5.0)
    watchdog.tick()

    assert [qc.command for qc in queued_commands(healthy)] == [ExpireDeadline(deadline.id)]


async def test_a_failed_enqueue_leaves_the_fence_clear_for_the_next_tick() -> None:
    """The fence is set before `submit` so no tick can see a queued expiry
    without one — but a `submit` that *raises* must roll it back. A fence
    left set behind a failed enqueue means nothing is queued and every
    later tick skips this deadline, so the game stalls on that window
    forever and the watchdog looks straight past it."""
    state = warmup_state()
    deadline = state.current_deadline()
    assert deadline is not None
    clock = FakeClock(deadline.deadline_at + timedelta(seconds=6))
    runtime = stalled_runtime(state, clock, queue_maxsize=1)
    runtime.submit(QueuedCommand(ExpireDeadline(DeadlineId(1)), "filler", RecordingOrigin()))

    # A non-`None` prior fence, as if an earlier deadline's expiry were
    # still genuinely pending. `None` alone cannot distinguish "restored
    # to the previous value" from a naive "reset to `None`" — a fresh
    # runtime's fence starts at `None` anyway, so without this sentinel
    # both a correct rollback and a rollback hardcoded to `None` would
    # read back identically.
    before = DeadlineId(4242)
    runtime.expiry_enqueued_deadline_id = before
    watchdog = Watchdog(manager=manager_holding(runtime), clock=clock, grace_s=5.0)

    watchdog.tick()  # ServerBusy: the queue is full
    assert runtime.expiry_enqueued_deadline_id == before  # rolled back to the prior value

    drain_queue(runtime)
    watchdog.tick()

    assert [qc.command for qc in queued_commands(runtime)] == [ExpireDeadline(deadline.id)]
    assert runtime.expiry_enqueued_deadline_id == deadline.id


async def test_the_loop_ticks_on_its_interval() -> None:
    """`tick()` is called directly above; this is the one test that drives
    `start()`, so the sleep loop itself is not untested code."""
    state = warmup_state()
    deadline = state.current_deadline()
    assert deadline is not None
    clock = FakeClock(deadline.deadline_at + timedelta(seconds=6))
    runtime = stalled_runtime(state, clock)
    watchdog = Watchdog(manager=manager_holding(runtime), clock=clock, interval_s=5.0, grace_s=5.0)
    watchdog.start()
    await clock.settle()
    assert queued_commands(runtime) == []  # nothing before the first tick

    await clock.advance_to(clock.now() + timedelta(seconds=5))

    assert [qc.command for qc in queued_commands(runtime)] == [ExpireDeadline(deadline.id)]
    await watchdog.aclose()
