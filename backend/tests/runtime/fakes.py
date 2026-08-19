"""The four fakes Spec 1 §12.2 asks for, in one place.

A test that needs a fifth should ask whether it is really testing the
runtime. These deliberately implement the Protocols in
`services.ports` structurally — never by subclassing them — so a drift
between port and fake shows up as a mypy error in the tests that pass
them, which is where it is cheapest to notice.
"""

import asyncio
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime

from triviador.domain.game.actions import RejectCode
from triviador.domain.game.events import GameEvent
from triviador.domain.game.state import GameState
from triviador.domain.ids import GameId
from triviador.services.ports import RuntimeCode

# Same instant as `tests.runtime.conftest.T0`. Duplicated rather than
# imported from there: `conftest.py` already imports from this module, and
# the reverse import would be circular. Task 16's API-side fixtures import
# this copy so a game built for a socket test carries the same fixed clock
# shape the rest of the runtime suite uses.
T0 = datetime(2026, 8, 17, 12, 0, tzinfo=UTC)


class FakeClock:
    """Time moves only when a test says so.

    `settle` exists because `create_task` does not run the coroutine —
    without it, a test that advances immediately after spawning a sleeper
    races the event loop and passes or fails depending on scheduling.
    Every test that spawns sleepers awaits `settle()` before its first
    `advance_to`.
    """

    def __init__(self, now: datetime) -> None:
        self._now = now
        self._waiters: list[tuple[datetime, asyncio.Event]] = []

    def now(self) -> datetime:
        return self._now

    async def sleep_until(self, when: datetime) -> None:
        if when <= self._now:
            # Mirrors SystemClock: a past instant resolves immediately and
            # never registers, so `pending()` reflects only real waits.
            return
        event = asyncio.Event()
        self._waiters.append((when, event))
        try:
            await event.wait()
        finally:
            # Covers cancellation, not just a normal wake: `advance_to`
            # already drops a due waiter before setting its event, so this
            # is a no-op there. Without it, a cancelled deadline task (the
            # ordinary case when a window retargets, §5.4) would leave a
            # phantom entry in `pending()` forever — the fake would keep
            # reporting a wait nothing is actually doing.
            self._waiters = [(w, e) for w, e in self._waiters if e is not event]

    def pending(self) -> tuple[datetime, ...]:
        return tuple(sorted(when for when, _ in self._waiters))

    async def advance_to(self, when: datetime) -> None:
        if when < self._now:
            raise ValueError(f"cannot rewind from {self._now} to {when}")
        self._now = when
        due = [(w, e) for w, e in self._waiters if w <= when]
        self._waiters = [(w, e) for w, e in self._waiters if w > when]
        for _, event in due:
            event.set()
        await self.settle()

    async def settle(self) -> None:
        """Let every runnable task reach its next await point.

        Three yields, not one: waking a sleeper typically starts a chain —
        the deadline task submits a command, the consumer picks it up, the
        consumer publishes — and each link needs a scheduling turn.
        """
        for _ in range(3):
            await asyncio.sleep(0)


@dataclass
class Published:
    game_id: GameId
    base_seq: int
    state: GameState
    events: tuple[GameEvent, ...]


class FakeBroadcaster:
    """`publish` records. `fail_with` makes it raise, which is Spec 1
    §12.2's "break the broadcaster after commit": the commit is durable,
    memory is correct, and the runtime must stay healthy."""

    def __init__(self) -> None:
        self.published: list[Published] = []
        self.fail_with: Exception | None = None

    def publish(
        self,
        game_id: GameId,
        base_seq: int,
        state: GameState,
        events: Sequence[GameEvent],
    ) -> None:
        if self.fail_with is not None:
            raise self.fail_with
        self.published.append(Published(game_id, base_seq, state, tuple(events)))


class FakeSubscribers:
    def __init__(self, counts: dict[GameId, int] | None = None) -> None:
        self.closed: list[tuple[GameId, int]] = []
        self.counts = counts if counts is not None else {}

    def close_game_subscribers(self, game_id: GameId, code: int) -> None:
        self.closed.append((game_id, code))

    def subscriber_count(self, game_id: GameId) -> int:
        return self.counts.get(game_id, 0)


@dataclass
class RecordingOrigin:
    """Records every resolution, so a test can assert exactly-once.

    Deliberately *not* idempotent and *not* non-throwing: those are
    properties of the real origins (Task 3), and a fake that quietly
    enforced them would hide a runtime that resolves twice.
    """

    resolutions: list[tuple[str, object]] = field(default_factory=list)

    def resolve_ok(self, events: Sequence[GameEvent]) -> None:
        self.resolutions.append(("ok", tuple(events)))

    def resolve_noop(self) -> None:
        self.resolutions.append(("noop", None))

    def resolve_rejected(self, code: RejectCode, message: str) -> None:
        self.resolutions.append(("rejected", code))

    def resolve_failed(self, code: RuntimeCode, message: str) -> None:
        self.resolutions.append(("failed", code))

    @property
    def outcome(self) -> tuple[str, object]:
        assert len(self.resolutions) == 1, (
            f"expected exactly one resolution, got {self.resolutions}"
        )
        return self.resolutions[0]
