"""Every capability the runtime needs, as a Protocol — and nothing else.

Spec 1B §5.1: "no implementation lives under `services/`". That is what
keeps `api → services → domain` one-directional and makes Spec 1 §12.2's
fake clock, breakable broadcaster and breakable commit mechanical rather
than heroic: each is a class satisfying a Protocol declared here, not a
monkeypatch of a concrete adapter.

`db/` imports this module to declare that its classes implement these
ports. This module imports `domain/` and `triviador.maps` and nothing
else; `tests/test_layering.py` enforces both halves.
"""

from collections.abc import Sequence
from contextlib import AbstractAsyncContextManager
from datetime import datetime
from enum import StrEnum
from typing import Protocol

from triviador.domain.game.actions import RejectCode
from triviador.domain.game.events import GameEvent
from triviador.domain.game.state import GameState
from triviador.domain.ids import GameId, MapId
from triviador.domain.questions.types import QuestionBudget, QuestionPool
from triviador.maps.registry import LoadedMap


class Clock(Protocol):
    """`sleep_until`, not `sleep`: Spec 1 §12.2 requires that no test waits
    on wall-clock time, and a duration-based API would force the fake clock
    to reconstruct absolute deadlines the runtime already computed."""

    def now(self) -> datetime: ...
    async def sleep_until(self, when: datetime) -> None: ...


class Broadcaster(Protocol):
    """Synchronous, and it takes domain objects.

    Synchronous because §8.6 forbids the runtime from awaiting a socket
    write, and a `def` cannot be awaited by accident. Domain objects
    because only the WebSocket layer knows each subscriber's
    `ViewerContext`, so §8.7's per-viewer projection must happen there.

    Contract, enforced by test rather than by signature: `publish` may only
    project and `put_nowait` — no awaits, no blocking I/O, no network, and
    no exception escaping. The signature only prevents `await`.
    """

    def publish(
        self,
        game_id: GameId,
        base_seq: int,
        state: GameState,
        events: Sequence[GameEvent],
    ) -> None: ...


class GameSubscriberControl(Protocol):
    """Sockets stay owned by the WebSocket hub; the runtime only ever asks
    it to act. `subscriber_count` exists for §5.6's reaper rule "LOBBY with
    no connections → runtime may be unloaded"."""

    def close_game_subscribers(self, game_id: GameId, code: int) -> None: ...
    def subscriber_count(self, game_id: GameId) -> int: ...


class QuestionPoolUnavailable(Exception):
    """The bank cannot supply a pool for this preset right now.

    The base class of Plan 3's `InsufficientQuestions` and
    `MalformedQuestion`. The materialiser catches exactly this and leaves
    `drawn_pool=None`, after which `decide` raises
    `RejectedCommand(QUESTION_POOL_INSUFFICIENT)` on its own — one policy,
    stated once, in the domain. A database *outage* raises something else
    entirely and is a fault, per §5.5.
    """


class EventStreamCorrupt(Exception):
    """A stored event cannot be turned back into a domain event.

    The base class of the codec's `UnknownEventType`,
    `UnknownSchemaVersion` and `NaiveDatetime`. Declared here for the
    same reason as `QuestionPoolUnavailable`: the runtime must be able to
    tell "this log will never decode" (permanent — go to `Failed`) from
    "the database is unreachable" (transient — retry with backoff), and
    it cannot import `db/` to ask.

    Naming a real type rather than matching on class-name strings is not
    style. A string match silently reclassifies every renamed or newly
    added decode error as *transient*, and a permanent failure retried
    forever is an invisible outage — the exact failure mode this split
    exists to prevent.
    """


class QuestionBankPort(Protocol):
    async def select_pool(self, budget: QuestionBudget) -> QuestionPool: ...


class EventRef(Protocol):
    """One committed row's identity, without its payload. Read-only
    properties, so a frozen dataclass satisfies it."""

    @property
    def seq(self) -> int: ...
    @property
    def type(self) -> str: ...


class ReconcileOutcome(StrEnum):
    """§5.5's ambiguous-commit verdict.

    The comparison lives behind the port because it needs the event→wire
    name mapping, which belongs to the codec — and `runtime/` may not
    import `db/`. `MISMATCH` is never "close enough": it quarantines.
    """

    MATCHED = "matched"
    ABSENT = "absent"
    MISMATCH = "mismatch"


class Transaction(Protocol):
    """Everything one command does to the database, inside one
    transaction. No `commit()`: the boundary belongs to
    `UnitOfWorkPort.begin`'s context manager, because §5.2 requires that an
    origin resolve only after that context exits."""

    @property
    def questions(self) -> QuestionBankPort: ...

    async def append(
        self,
        game_id: GameId,
        *,
        expected_last_seq: int,
        events: Sequence[GameEvent],
        operation_id: str,
    ) -> None: ...

    async def load_stream(self, game_id: GameId) -> tuple[GameEvent, ...]: ...

    async def events_for_operation(
        self, game_id: GameId, operation_id: str
    ) -> tuple[EventRef, ...]: ...

    # `operation_matches` joins this Protocol in Task 6, together with the
    # adapter method that satisfies it. Declaring it here first would
    # leave the conformance check red for five tasks, which is a broken
    # build, not a red test.


class UnitOfWorkPort(Protocol):
    def begin(self) -> AbstractAsyncContextManager[Transaction]: ...


class GameQueriesPort(Protocol):
    """Only what the runtime asks of the games table. Plan 5 widens this
    for the REST surface (`create`, `get_summary`, `list_joinable`); the
    runtime has no business knowing about those."""

    async def find_empty_lobbies(self, *, created_before: datetime) -> tuple[GameId, ...]: ...
    async def find_stale_lobbies(self, *, created_before: datetime) -> tuple[GameId, ...]: ...
    async def find_unfinished(self) -> tuple[GameId, ...]: ...


class MapProvider(Protocol):
    def load_with_digest(self, map_id: MapId) -> LoadedMap: ...


class RuntimeCode(StrEnum):
    """Transport-level outcomes, disjoint from `RejectCode`.

    A `RejectCode` says the client should not have sent this command; a
    `RuntimeCode` says the server could not take it right now and the
    client should retry or give up. Keeping them separate stops a 503
    condition from being rendered as a 409.
    """

    SERVER_BUSY = "server_busy"
    SERVER_RESTARTING = "server_restarting"
    GAME_RECOVERING = "game_recovering"
    GAME_UNRECOVERABLE = "game_unrecoverable"


class Origin(Protocol):
    """Whoever is waiting for a command's outcome.

    Every method is non-throwing and idempotent: a REST client can
    disconnect while its command is in the queue, leaving a cancelled
    future whose `set_result` raises `InvalidStateError` *after* the batch
    has already committed. If that propagated, a delivery failure on a dead
    HTTP request would quarantine a game whose state is durable and
    correct (§5.2).
    """

    def resolve_ok(self, events: Sequence[GameEvent]) -> None: ...
    def resolve_noop(self) -> None: ...
    def resolve_rejected(self, code: RejectCode, message: str) -> None: ...
    def resolve_failed(self, code: RuntimeCode, message: str) -> None: ...
