"""§5.2. The loop is short; the ordering inside it is the whole point."""

import random
from collections.abc import Sequence
from dataclasses import dataclass

import pytest

from tests.conftest import lobby_state
from tests.runtime.conftest import T0, GatedExecutor, StubExecutor
from tests.runtime.conftest import drain_runtime as settle
from tests.runtime.fakes import FakeBroadcaster, FakeClock, RecordingOrigin
from triviador.domain.game.actions import ExpireDeadline, JoinGame, RejectCode
from triviador.domain.game.events import GameEvent, PlayerJoined
from triviador.domain.game.state import GameState
from triviador.domain.ids import DeadlineId, GameId, PlayerId
from triviador.runtime.commit import Executor
from triviador.runtime.errors import CommitFault, RuntimeClosed, ServerBusy
from triviador.runtime.origins import Accepted, Ignored, Rejected
from triviador.runtime.runtime import GameRuntime, QueuedCommand
from triviador.services.ports import RuntimeCode


@dataclass
class TracingOrigin:
    """Appends to a shared trace so ordering against the broadcaster is
    observable, which `RecordingOrigin` alone cannot show."""

    trace: list[str]

    def resolve_ok(self, events: Sequence[GameEvent]) -> None:
        self.trace.append("resolve_ok")

    def resolve_noop(self) -> None:
        self.trace.append("resolve_noop")

    def resolve_rejected(self, code: RejectCode, message: str) -> None:
        self.trace.append("resolve_rejected")

    def resolve_failed(self, code: RuntimeCode, message: str) -> None:
        self.trace.append("resolve_failed")


class TracingBroadcaster(FakeBroadcaster):
    def __init__(self, trace: list[str]) -> None:
        super().__init__()
        self._trace = trace

    def publish(
        self,
        game_id: GameId,
        base_seq: int,
        state: GameState,
        events: Sequence[GameEvent],
    ) -> None:
        self._trace.append("publish")
        super().publish(game_id, base_seq, state, events)


def a_runtime(
    executor: Executor,
    *,
    broadcaster: FakeBroadcaster | None = None,
    faults: list[tuple[object, BaseException]] | None = None,
    queue_maxsize: int = 256,
) -> GameRuntime:
    def on_fault(rt: GameRuntime, exc: BaseException) -> None:
        if faults is not None:
            faults.append((rt, exc))

    return GameRuntime(
        state=lobby_state(players={"p1": 0}),
        executor=executor,
        clock=FakeClock(T0),
        broadcaster=broadcaster if broadcaster is not None else FakeBroadcaster(),
        on_fault=on_fault,
        generation=17,
        rng=random.Random(0),
        queue_maxsize=queue_maxsize,
    )


async def test_a_committed_command_folds_publishes_and_resolves_in_that_order() -> None:
    """Fold, then publish, then resolve. Publishing a state that has not
    folded the batch sends clients a snapshot that contradicts the events
    beside it; resolving before publishing lets a REST caller observe its
    own write before any subscriber does."""
    event = PlayerJoined(PlayerId("p2"), "P2", seat=1)
    trace: list[str] = []
    broadcaster = TracingBroadcaster(trace)
    runtime = a_runtime(StubExecutor([Accepted((event,))]), broadcaster=broadcaster)
    runtime.start()
    origin = TracingOrigin(trace)

    runtime.submit(QueuedCommand(JoinGame(PlayerId("p2"), "P2"), "op-1", origin))
    await settle(runtime)

    assert PlayerId("p2") in runtime.state.players
    assert trace == ["publish", "resolve_ok"]
    assert PlayerId("p2") in broadcaster.published[0].state.players  # folded before publish
    await runtime.aclose()


async def test_an_ignored_command_does_not_evolve_publish_or_reschedule() -> None:
    """§5.2: a no-op resolves and `continue`s — no evolve, no reschedule,
    no publish. A stale window is a benign race, and broadcasting a
    state that did not change would make every client re-render for
    nothing."""
    broadcaster = FakeBroadcaster()
    runtime = a_runtime(StubExecutor([Ignored()]), broadcaster=broadcaster)
    runtime.start()
    before = runtime.state
    origin = RecordingOrigin()

    runtime.submit(QueuedCommand(ExpireDeadline(DeadlineId(99)), "op-1", origin))
    await settle(runtime)

    assert runtime.state is before
    assert broadcaster.published == []
    assert origin.outcome == ("noop", None)
    await runtime.aclose()


async def test_a_rejected_command_leaves_state_untouched_and_the_runtime_healthy() -> None:
    """§5.5: rollback, reply to origin, state untouched, runtime healthy.
    The second command proves the last clause — a loop that stopped
    consuming after a rejection would hang every later request."""
    executor = StubExecutor([Rejected(RejectCode.GAME_FULL, "lobby is full"), Ignored()])
    broadcaster = FakeBroadcaster()
    runtime = a_runtime(executor, broadcaster=broadcaster)
    runtime.start()
    before = runtime.state
    rejected, followup = RecordingOrigin(), RecordingOrigin()

    runtime.submit(QueuedCommand(JoinGame(PlayerId("p9"), "P9"), "op-1", rejected))
    await settle(runtime)
    runtime.submit(QueuedCommand(ExpireDeadline(DeadlineId(99)), "op-2", followup))
    await settle(runtime)

    assert runtime.state is before
    assert broadcaster.published == []
    assert rejected.outcome == ("rejected", RejectCode.GAME_FULL)
    assert followup.outcome == ("noop", None)
    assert runtime.closed is False
    await runtime.aclose()


async def test_publish_receives_the_pre_command_base_seq_and_the_post_command_state() -> None:
    """§8.2's dispatcher needs the *pre*-command seq to detect a gap:
    a client holding seq N applies a frame whose `base_seq` is N and
    resyncs otherwise. Publishing the post-fold seq would make every
    client believe it is already up to date and silently skip the gap."""
    event = PlayerJoined(PlayerId("p2"), "P2", seat=1)
    broadcaster = FakeBroadcaster()
    runtime = a_runtime(StubExecutor([Accepted((event,))]), broadcaster=broadcaster)
    runtime.start()
    base_seq = runtime.state.seq

    runtime.submit(QueuedCommand(JoinGame(PlayerId("p2"), "P2"), "op-1", RecordingOrigin()))
    await settle(runtime)

    published = broadcaster.published[0]
    assert published.base_seq == base_seq
    assert published.state is runtime.state
    assert published.state.seq == base_seq + 1
    await runtime.aclose()


async def test_a_broadcaster_that_raises_never_faults_the_runtime() -> None:
    """§5.5: the commit is durable and memory is correct. Destroying a
    healthy runtime over a misbehaving socket converts a client problem
    into a game-wide outage, and §8.5 already gives every client an
    unconditional recovery path."""
    event = PlayerJoined(PlayerId("p2"), "P2", seat=1)
    broadcaster = FakeBroadcaster()
    broadcaster.fail_with = RuntimeError("socket gone")
    faults: list[tuple[object, BaseException]] = []
    runtime = a_runtime(StubExecutor([Accepted((event,))]), broadcaster=broadcaster, faults=faults)
    runtime.start()
    origin = RecordingOrigin()

    runtime.submit(QueuedCommand(JoinGame(PlayerId("p2"), "P2"), "op-1", origin))
    await settle(runtime)

    assert faults == []
    assert runtime.closed is False
    assert origin.outcome == ("ok", (event,))
    assert PlayerId("p2") in runtime.state.players
    await runtime.aclose()


async def test_a_commit_fault_reports_to_the_manager_and_stops_the_loop() -> None:
    """The dequeued command's origin is resolved *here*, not by the
    manager's drain: quarantine drains the queue, and this command is no
    longer in it. Then the loop stops — teardown must not run on this
    task, because a task cannot cancel and await itself."""
    faults: list[tuple[object, BaseException]] = []
    runtime = a_runtime(StubExecutor([CommitFault("boom")]), faults=faults)
    runtime.start()
    origin = RecordingOrigin()

    runtime.submit(QueuedCommand(JoinGame(PlayerId("p2"), "P2"), "op-1", origin))
    await settle(runtime)

    assert len(faults) == 1
    assert faults[0][0] is runtime
    assert origin.outcome == ("failed", RuntimeCode.GAME_RECOVERING)
    assert runtime.consumer_done()
    # The loop stopped, but the *manager* closes the runtime — not the
    # faulting task. Until quarantine runs, submit still accepts.
    runtime.submit(QueuedCommand(JoinGame(PlayerId("p3"), "P3"), "op-2", RecordingOrigin()))
    assert runtime.pending_commands() == 1
    await runtime.aclose()


async def test_submit_on_a_full_queue_raises_without_resolving_the_origin() -> None:
    """An origin belongs to the caller until `submit` returns
    successfully. Resolving here as well as raising would be a double
    resolution the moment the caller handles the raise."""
    runtime = a_runtime(StubExecutor([]), queue_maxsize=1)  # not started: nothing drains it
    accepted, refused = RecordingOrigin(), RecordingOrigin()

    runtime.submit(QueuedCommand(JoinGame(PlayerId("p2"), "P2"), "op-1", accepted))
    with pytest.raises(ServerBusy):
        runtime.submit(QueuedCommand(JoinGame(PlayerId("p3"), "P3"), "op-2", refused))

    assert refused.resolutions == []
    assert accepted.resolutions == []


async def test_submit_on_a_closed_runtime_raises_runtime_closed() -> None:
    runtime = a_runtime(StubExecutor([]))
    runtime.start()
    await runtime.aclose()

    with pytest.raises(RuntimeClosed):
        runtime.submit(QueuedCommand(JoinGame(PlayerId("p2"), "P2"), "op-1", RecordingOrigin()))


async def test_drain_resolves_every_queued_origin_once_with_the_given_code() -> None:
    """Used by quarantine (`GAME_RECOVERING`) and shutdown
    (`SERVER_RESTARTING`) — the two places where queued commands will
    never be processed and their origins would otherwise hang forever."""
    runtime = a_runtime(StubExecutor([]))  # not started
    first, second = RecordingOrigin(), RecordingOrigin()
    runtime.submit(QueuedCommand(JoinGame(PlayerId("p2"), "P2"), "op-1", first))
    runtime.submit(QueuedCommand(JoinGame(PlayerId("p3"), "P3"), "op-2", second))

    drained = runtime.drain(RuntimeCode.SERVER_RESTARTING, "server is restarting")

    assert drained == 2
    assert first.outcome == ("failed", RuntimeCode.SERVER_RESTARTING)
    assert second.outcome == ("failed", RuntimeCode.SERVER_RESTARTING)
    assert runtime.pending_commands() == 0


async def test_in_flight_is_true_only_while_a_command_is_executing() -> None:
    """`is_idle()` is what stops the reaper cancelling a live
    transaction, and it is only as good as this flag. The queue reads
    empty for the whole duration of a command, because `_consume`
    dequeues before it executes."""
    executor = GatedExecutor()
    runtime = a_runtime(executor)
    runtime.start()
    assert runtime.is_idle()

    runtime.submit(QueuedCommand(JoinGame(PlayerId("p2"), "P2"), "op-1", RecordingOrigin()))
    await executor.entered.wait()

    assert runtime.pending_commands() == 0  # the lie is_idle() exists to correct
    assert not runtime.is_idle()

    executor.release.set()
    await settle(runtime)
    assert runtime.is_idle()
    await runtime.aclose()
