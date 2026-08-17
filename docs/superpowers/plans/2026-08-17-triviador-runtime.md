# Triviador Plan 4 — Runtime Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make a game *run*. One `GameRuntime` per game owns a bounded command queue, a single-threaded consumer loop that turns a command into committed events inside one transaction, and a one-shot deadline task; one `GameManager` owns every runtime plus the watchdog and reaper, loads games on demand and at boot, and quarantines and reloads a runtime whose attempt failed. After this plan a game can be played from `LOBBY` to `FINISHED` by submitting commands to the manager — with no HTTP, no WebSocket, and no frontend anywhere in the picture.

**Architecture:** Plan 3 built the durable layer; this plan builds the thing that drives it. The domain stays pure — `decide` and `evolve` never learn what a clock, a database, or a socket is. Every non-deterministic input is resolved into a `DecisionContext` by a *materialiser* running inside the same transaction that later appends, so replay never diverges (ADR-004). Capabilities enter through `services/ports.py`, which declares Protocols and nothing else: `runtime/` depends on those Protocols, `db/` implements them, and neither imports the other.

**Tech Stack:** Python 3.13 · `uv` · `asyncio` · SQLAlchemy 2.0 (async) · `asyncpg` · PostgreSQL 17 · `ruff` · `mypy --strict` · `pytest` + `pytest-asyncio` (`asyncio_mode = "auto"`) · Hypothesis

**Spec:** `docs/superpowers/specs/2026-08-16-triviador-app-architecture-design.md` §5 (ports, consumer loop, one transaction per command, deadlines, failure policy, `GameManager`), §5.6 (recovery, generation fencing, watchdog, reaper, shutdown), §11 (the runtime test list) · Spec 1 `docs/superpowers/specs/2026-08-07-triviador-spec1-design.md` §3.4 (bases must be mutually non-adjacent), §12.2 (the Layer 2 scenario list), §4 (layering rule)

---

## Global Constraints

Every task's requirements implicitly include this section.

- **The domain stays pure.** `domain/` must not import `db/`, `services/`, `runtime/`, `api/`, `sqlalchemy`, `asyncpg`, `alembic`, or `pydantic`. `tests/test_layering.py` already proves it; this plan extends that test rather than weakening it.
- **`services/` declares Protocols and nothing else.** No implementation, no `db` import, no `runtime` import, no `sqlalchemy`. It may import `domain/` and `triviador.maps`. Spec 1B §5.1 is explicit: "no implementation lives under `services/`."
- **`runtime/` never imports `db/`.** It receives every capability through `services/ports.py`. The composition root that constructs concrete adapters is Plan 5's `api/app.py`; until then it is the test fixtures. A single `from triviador.db...` line inside `runtime/` defeats the entire port layer, so the layering test forbids it.
- **No test waits on wall-clock time.** `Clock.sleep_until` takes an absolute instant and the fake clock is driven by explicit `advance_to`. A `sleep(0.1)` anywhere in this plan's tests is a defect, not a shortcut (Spec 1 §12.2).
- **Every origin resolves exactly once** — on ignore, on reject, on success, on quarantine, on shutdown, and on a full queue. An unresolved origin is a hung HTTP request.
- **Origin resolution is non-throwing and idempotent.** A transport delivery failure is logged and never reaches runtime fault handling (§5.2). A second `resolve_*` call is a no-op.
- **No external response is produced while database locks are held.** Origins resolve only after the transaction context exits (§5.2).
- **The broadcaster is synchronous and never faults the runtime.** `publish` is a `def`, may only project and `put_nowait`, and an exception out of it is logged and swallowed (§5.5).
- **Quarantine never runs on the faulting task.** A task cannot cancel and await itself; teardown is scheduled onto the manager (§5.6).
- **`NewType` aliases are constructed, never implied.** `PlayerId`, `GameId`, `RegionId`, `MapId` and `DeadlineId` are `NewType`s (`domain/ids.py`), so `PlayerJoined("p1", ...)` fails `mypy --strict` even though it runs. Every literal in production code and in tests goes through its constructor: `PlayerJoined(PlayerId("p1"), "P1", seat=0)`.
- **Commands carry their actor.** `StartGame`, `JoinGame`, `SubmitAnswer`, `PickRegion`, `SelectAttackTarget` and `Surrender` all take `actor_id: PlayerId` as their first field; only `ExpireDeadline` and `AbortGame` do not (`AbortGame`'s is optional, and `None` means system-issued). Check `domain/game/actions.py` before constructing one — a command built with the wrong arity is a task that cannot reach its own red state.
- **Every timestamp is timezone-aware UTC.** `Clock.now()` returns an aware `datetime`; a naive one is a bug that only surfaces after a process restart, when an absolute deadline is compared across it.
- **Recovery honours absolute deadlines, never restarts them** (ADR-003, §5.6). A window a player has already spent must not be extended.
- Python `>=3.13`. Line length 100. `ruff check`, `ruff format --check`, and `mypy --strict` must pass on every commit.
- **`reducer.py` and `db/codec/` keep their 100 % branch coverage gates.** Nothing in this plan may lower either. `runtime/` is measured but not gated.
- **Integration tests run against real PostgreSQL, never SQLite,** carry `pytestmark = pytest.mark.integration`, and fail loudly rather than skipping when the database is absent — the rules `tests/db/conftest.py` already enforces.

---

## File Structure

```
backend/
├── pyproject.toml                       MODIFY  no new runtime deps; test paths only
└── src/triviador/
    ├── config.py                        MODIFY  runtime settings block
    ├── services/
    │   ├── __init__.py                  CREATE
    │   └── ports.py                     CREATE  every Protocol; no implementation
    ├── domain/maps/
    │   └── placement.py                 CREATE  choose_base_regions — pure, rng passed in
    ├── db/
    │   ├── errors.py                    MODIFY  bank errors subclass the port's exception
    │   └── unit_of_work.py              MODIFY  `questions` property, `operation_matches`
    └── runtime/
        ├── __init__.py                  CREATE
        ├── clock.py                     CREATE  SystemClock
        ├── origins.py                   CREATE  SystemOrigin, FutureOrigin
        ├── errors.py                    CREATE  RuntimeClosed, ServerBusy, GameRecovering, …
        ├── materialiser.py              CREATE  Command → DecisionContext, inside the tx
        ├── loader.py                    CREATE  log → GameState, permanent/transient split
        ├── commit.py                    CREATE  one attempt: retry, reconcile, classify
        ├── runtime.py                   CREATE  GameRuntime: queue, consumer loop, deadline
        ├── manager.py                   CREATE  registry, load-once, quarantine, shutdown
        ├── watchdog.py                  CREATE
        └── reaper.py                    CREATE

backend/tests/
├── test_layering.py                     MODIFY  services/ and runtime/ rules
├── services/
│   ├── __init__.py                      CREATE
│   └── test_ports.py                    CREATE  conformance, checked by mypy
├── domain/maps/test_placement.py        CREATE
└── runtime/                             ← PURE unless the module says otherwise.
    ├── __init__.py                      CREATE
    ├── fakes.py                         CREATE  FakeClock, FakeBroadcaster, FakeUnitOfWork, …
    ├── conftest.py                       CREATE  builders shared by the runtime suite
    ├── test_clock.py                    CREATE
    ├── test_origins.py                  CREATE
    ├── test_materialiser.py             CREATE
    ├── test_loader.py                   CREATE
    ├── test_commit.py                   CREATE
    ├── test_runtime_loop.py             CREATE
    ├── test_deadlines.py                CREATE
    ├── test_manager.py                  CREATE
    ├── test_quarantine.py               CREATE
    ├── test_watchdog.py                 CREATE
    ├── test_reaper.py                   CREATE
    ├── test_shutdown.py                 CREATE
    └── integration/                     ← INTEGRATION ONLY. Every module marked.
        ├── __init__.py                  CREATE
        ├── conftest.py                  CREATE  wires real adapters onto tests/db fixtures
        ├── test_play_through.py         CREATE  lobby → finished, real PostgreSQL
        ├── test_ambiguous_commit.py     CREATE
        └── test_recovery.py             CREATE
```

**Why `runtime/` is a package of small modules rather than two large ones.** `GameRuntime` and `GameManager` are the two objects the spec names, but the hard parts — materialisation, replay classification, retry/reconciliation — are each independently testable against fakes and each carry their own failure taxonomy. Splitting them means a task can be reviewed and rejected on its own, and `runtime.py` stays a loop you can hold in your head.

---

## Design decisions this plan makes that the spec does not state

Three gaps in Spec 1B §5 became visible while grounding this plan against the code Plans 2 and 3 actually produced. Each is resolved here, in the open, rather than discovered mid-task:

1. **A malformed question row is a rejection, not a fault.** §5.5 maps "insufficient bank at `StartGame`" to an ordinary rejection and "exception in materialiser (database)" to quarantine. `MalformedQuestion` (Plan 3) is neither: it is bad *content*, not a broken database. Quarantining on it would convert one bad row into a permanent reload loop for a game sitting harmlessly in `LOBBY` — the same reasoning §5.5 already uses to keep broadcaster failure out of fault handling. Task 4 treats it exactly like `InsufficientQuestions`: log at error with the `question_id`, leave `drawn_pool=None`, and let `decide` raise `RejectedCommand(QUESTION_POOL_INSUFFICIENT)`.

2. **The materialiser owns non-adjacency of bases.** Spec 1 §3.4 requires `BasesAssigned` to name `player_count` *mutually non-adjacent* regions, and the map validator guarantees such a set exists for every registered map. But `_decide_start` (Plan 2) validates only that `base_regions` are distinct and on the map — it never checks adjacency, because the domain does not choose them. Nothing in the system enforces §3.4 today. Task 3 adds `choose_base_regions` as a pure domain function with an exhaustive randomized search, so the rule is enforced where the regions are actually picked.

3. **Reconciliation compares wire names, so it belongs behind the port.** §5.5 requires verifying the ordered *types* of the committed batch against the batch in memory. The event→wire-name mapping lives in `db/codec/registry.py`, and `runtime/` may not import `db/`. Task 6 therefore puts the whole comparison behind `Transaction.operation_matches(...) -> ReconcileOutcome`, returning `MATCHED` / `ABSENT` / `MISMATCH`. The runtime branches on three outcomes and never learns a wire name.

Two further items are deliberate *narrowings* of §5.1's port list:

- **`MediaStore` and `ImportStagingStore` are omitted.** Nothing in Plan 4 or Plan 5 calls either — they are Plan 7's admin media pipeline. Declaring them now would mean writing a Protocol with no implementer and no caller, and getting its shape wrong in the quiet way that only surfaces when Plan 7 has to change it anyway. Plan 7 adds them.
- **`GameEventStore` folds into `Transaction`.** §5.1 names it as a separate port, but Plan 3 put `append`, `load_stream` and `events_for_operation` on `TransactionContext` rather than on a standalone store — deliberately, because §5.3 requires selection and append to share one unit of work for *every* command. A separate `GameEventStore` port would have to be handed the transaction on every call, which is the same coupling with an extra object. The capability is fully declared; it is declared as three methods on `Transaction` instead of as a fourth Protocol.

---

## Task 1: Ports, runtime settings, and the layering rules that keep them honest

**Files:**
- Create: `backend/src/triviador/services/__init__.py`, `backend/src/triviador/services/ports.py`
- Modify: `backend/src/triviador/config.py`, `backend/src/triviador/db/errors.py`
- Test: `backend/tests/services/__init__.py`, `backend/tests/services/test_ports.py`, `backend/tests/test_layering.py` (modify)

**Interfaces:**
- Consumes: `db.unit_of_work.UnitOfWork` / `TransactionContext`, `db.repositories.games.GameRepository`, `db.repositories.questions.QuestionBank`, `maps.registry.MapRegistry` — the concrete classes that must satisfy the new Protocols.
- Produces: `services.ports.{Clock, Broadcaster, GameSubscriberControl, Transaction, UnitOfWorkPort, QuestionBankPort, GameQueriesPort, MapProvider, EventRef, ReconcileOutcome, Origin, QuestionPoolUnavailable}` and `config.Settings` fields `command_queue_maxsize`, `watchdog_interval_s`, `watchdog_grace_s`, `reaper_interval_s`, `empty_lobby_grace_minutes`, `lobby_max_age_hours`, `recovery_backoff_initial_s`, `recovery_backoff_max_s`, `commit_max_attempts`. Every later task depends on these names.

**Why `db/` imports `services/` and not the reverse.** `services/ports.py` is the contract; `db/` is an adapter that implements it. An adapter naming its contract is the correct direction — it is what lets `runtime/` depend on the contract alone. The rule the layering test enforces is one-way: `services/` must never import `db/`.

- [ ] **Step 1: Write the failing conformance and layering tests**

`backend/tests/services/__init__.py` is empty. `backend/tests/services/test_ports.py`:

```python
"""Conformance is a *typing* property, so mypy is the test.

`_conformance` never runs — it lives under `TYPE_CHECKING` and takes the
concrete Plan 3 classes as parameters. Assigning each to its Protocol is
what proves the adapter satisfies the port; `uv run mypy` failing with
"Incompatible types in assignment" is this module's red state. The two
runtime tests below cover the parts mypy cannot see: that the ports module
imports no persistence code, and that the exception hierarchy the
materialiser catches actually holds.
"""

from typing import TYPE_CHECKING

from triviador.db.errors import InsufficientQuestions, MalformedQuestion
from triviador.domain.ids import QuestionId
from triviador.domain.questions.types import QuestionKind
from triviador.services import ports

if TYPE_CHECKING:
    from triviador.db.repositories.games import GameRepository
    from triviador.db.repositories.questions import QuestionBank
    from triviador.db.unit_of_work import TransactionContext, UnitOfWork
    from triviador.maps.registry import MapRegistry

    def _conformance(
        uow: UnitOfWork,
        tx: TransactionContext,
        repo: GameRepository,
        bank: QuestionBank,
        registry: MapRegistry,
    ) -> None:
        _uow: ports.UnitOfWorkPort = uow
        _tx: ports.Transaction = tx
        _repo: ports.GameQueriesPort = repo
        _bank: ports.QuestionBankPort = bank
        _maps: ports.MapProvider = registry


def test_bank_shortfalls_are_catchable_as_one_port_exception() -> None:
    """The materialiser catches a single type. If either bank error stops
    subclassing `QuestionPoolUnavailable`, a content problem starts
    quarantining a healthy lobby instead of rejecting a StartGame."""
    insufficient = InsufficientQuestions(kind=QuestionKind.NUMERIC, required=17, available=3)
    malformed = MalformedQuestion(question_id=QuestionId("q1"), kind=QuestionKind.NUMERIC)

    assert isinstance(insufficient, ports.QuestionPoolUnavailable)
    assert isinstance(malformed, ports.QuestionPoolUnavailable)


def test_ports_module_imports_no_persistence_code() -> None:
    """A Protocol that mentions a SQLAlchemy type is not a port — it is a
    re-export of the adapter, and `runtime/` would inherit the dependency
    through it."""
    source = (
        __import__("pathlib").Path(ports.__file__).read_text(encoding="utf-8")
    )
    for forbidden in ("sqlalchemy", "asyncpg", "triviador.db", "triviador.runtime"):
        assert forbidden not in source, f"services/ports.py must not mention {forbidden}"
```

Append to `backend/tests/test_layering.py` — read the existing module first and reuse its import-scanning helper rather than writing a second one:

```python
def test_services_does_not_import_adapters() -> None:
    """`services/` is the contract layer. It may name `domain` and
    `triviador.maps` (both pure); naming `db`, `runtime`, or `api` would
    make the contract depend on an implementation of itself."""
    violations = _imports_matching(
        SRC / "services",
        forbidden=("triviador.db", "triviador.runtime", "triviador.api",
                   "sqlalchemy", "asyncpg", "alembic"),
    )
    assert violations == [], violations


def test_runtime_does_not_import_persistence_or_api() -> None:
    """Every capability the runtime uses arrives through `services.ports`.
    One `from triviador.db...` here and the port layer is decoration."""
    violations = _imports_matching(
        SRC / "runtime",
        forbidden=("triviador.db", "triviador.api", "sqlalchemy", "asyncpg", "alembic"),
    )
    assert violations == [], violations
```

If `test_layering.py`'s existing helper has a different name or signature, adapt these two tests to it — do not introduce a parallel implementation. The helper must already catch the relative-import form (`from ..db import x`), because Plan 3's done criteria required it.

- [ ] **Step 2: Run the tests and watch them fail**

```bash
cd backend && uv run pytest tests/services tests/test_layering.py -q --no-cov
uv run mypy
```

Expected: pytest fails at import (`No module named 'triviador.services'`); after the module exists but before `db/errors.py` changes, `test_bank_shortfalls_are_catchable_as_one_port_exception` fails on `isinstance`.

- [ ] **Step 3: Write `services/ports.py`**

```python
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
from triviador.domain.ids import GameId
from triviador.domain.questions.types import QuestionBudget, QuestionPool
from triviador.maps.registry import LoadedMap
from triviador.domain.ids import MapId


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
```

- [ ] **Step 4: Reparent the db exceptions onto the port's base classes**

In `backend/src/triviador/db/errors.py`, add the import and change five class headers:

```python
from triviador.services.ports import EventStreamCorrupt, QuestionPoolUnavailable
```

```python
class UnknownEventType(EventStreamCorrupt):
class UnknownSchemaVersion(EventStreamCorrupt):
class NaiveDatetime(EventStreamCorrupt):
class InsufficientQuestions(QuestionPoolUnavailable):
class MalformedQuestion(QuestionPoolUnavailable):
```

Keep every docstring and every `__init__` body exactly as it is. Add one line to each docstring recording the new base class and why it is there: the runtime catches one type per category, so a content shortfall stays a rejection and a corrupt log is classified as permanent — neither by guessing at a class name.

Extend `backend/tests/services/test_ports.py`:

```python
def test_decode_failures_are_catchable_as_one_port_exception() -> None:
    """The loader's permanent/transient split hangs off this. If a decode
    error stops subclassing `EventStreamCorrupt`, an undecodable log gets
    classified transient and retried with backoff forever — an outage
    with no error to find."""
    from triviador.db.errors import NaiveDatetime, UnknownEventType, UnknownSchemaVersion

    for error in (
        UnknownEventType("battle.unheard_of"),
        UnknownSchemaVersion("battle.duel_resolved", 9),
        NaiveDatetime("turn.deadline.deadline_at"),
    ):
        assert isinstance(error, ports.EventStreamCorrupt)
```

- [ ] **Step 5: Add the runtime settings**

In `backend/src/triviador/config.py`, inside `Settings`, below `database_url`:

```python
    # Runtime tunables (Spec 1B §5.6). Every one has a default because,
    # unlike `database_url`, a wrong-but-plausible value here degrades
    # behaviour rather than pointing the process at the wrong data — and a
    # deployment that must set nine environment variables to boot is a
    # deployment that will set one of them wrong.
    command_queue_maxsize: int = 256
    commit_max_attempts: int = 3
    watchdog_interval_s: float = 5.0
    watchdog_grace_s: float = 5.0
    reaper_interval_s: float = 60.0
    empty_lobby_grace_minutes: int = 5
    lobby_max_age_hours: int = 6
    recovery_backoff_initial_s: float = 1.0
    recovery_backoff_max_s: float = 60.0
```

Add to `backend/tests/services/test_ports.py`:

```python
def test_runtime_settings_defaults() -> None:
    """§5.6's numbers, in one place. 256 sits far above any legitimate
    burst from four players; 5 s is the watchdog tick *and* its grace."""
    from triviador.config import Settings

    settings = Settings(database_url="postgresql+asyncpg://u:p@localhost/db")

    assert settings.command_queue_maxsize == 256
    assert settings.commit_max_attempts == 3
    assert settings.watchdog_interval_s == 5.0
    assert settings.watchdog_grace_s == 5.0
    assert settings.lobby_max_age_hours == 6
    assert settings.empty_lobby_grace_minutes == 5
```

- [ ] **Step 6: Add `questions` to `TransactionContext`**

`services.ports.Transaction` requires it, and it is how the materialiser reaches the bank without the port surface ever mentioning `AsyncSession`. In `backend/src/triviador/db/unit_of_work.py`:

```python
    @property
    def questions(self) -> QuestionBank:
        """A `QuestionBank` bound to *this* transaction's session.

        Selection and append share one unit of work for every command
        (§5.3), so the `FOR SHARE` locks the bank takes are still held when
        the resulting `QuestionPoolDrawn` event is inserted. Exposing the
        bank rather than the raw `session` is what keeps `AsyncSession` out
        of `services.ports.Transaction` — and therefore out of every
        signature `runtime/` can see.
        """
        return QuestionBank(self.session)
```

Keep the existing `self.session` attribute: `tests/db` uses it, and Plan 5's admin paths will too.

`ReconcileOutcome` is declared on the port in this task (the runtime's `Transaction` needs the enum before the method exists), but `operation_matches` itself lands in Task 6 on both sides at once — Protocol and adapter together. The conformance assignment `_tx: ports.Transaction = tx` must be green at the end of *every* task, so a method may never be declared on a port one task ahead of the adapter that satisfies it.

- [ ] **Step 7: Verify**

```bash
cd backend && uv run pytest tests/services tests/test_layering.py tests/db -q --no-cov
uv run pytest -q
uv run ruff check . && uv run ruff format --check . && uv run mypy
```

Expected: all green, including the existing Plan 3 suites. `tests/db` must still pass — `db/errors.py` and `db/unit_of_work.py` both changed.

- [ ] **Step 8: Commit**

```bash
git add backend/src/triviador/services backend/src/triviador/config.py \
        backend/src/triviador/db/errors.py backend/src/triviador/db/unit_of_work.py \
        backend/tests/services backend/tests/test_layering.py
git commit -m "feat(services): capability ports, runtime settings, and layering rules"
```

---

## Task 2: `SystemClock`, `FakeClock`, and the fakes every later task builds on

**Files:**
- Create: `backend/src/triviador/runtime/__init__.py`, `backend/src/triviador/runtime/clock.py`
- Test: `backend/tests/runtime/__init__.py`, `backend/tests/runtime/fakes.py`, `backend/tests/runtime/test_clock.py`

**Interfaces:**
- Consumes: `services.ports.{Clock, Broadcaster, GameSubscriberControl}` (Task 1).
- Produces: `runtime.clock.SystemClock`; `tests.runtime.fakes.{FakeClock, FakeBroadcaster, FakeSubscribers, RecordingOrigin}`. Tasks 3–15 build every scenario out of these four fakes — no later task writes its own clock.

- [ ] **Step 1: Write the failing tests**

`backend/tests/runtime/__init__.py` is empty. `backend/tests/runtime/test_clock.py`:

```python
"""The fake clock is load-bearing for every later task, so it gets tested
like production code. If `advance_to` can wake a sleeper early or leave a
due one asleep, a dozen downstream tests become quietly meaningless."""

import asyncio
from datetime import UTC, datetime, timedelta

from tests.runtime.fakes import FakeClock
from triviador.runtime.clock import SystemClock

T0 = datetime(2026, 8, 17, 12, 0, tzinfo=UTC)


def test_system_clock_returns_aware_utc() -> None:
    """A naive datetime here survives until a process restart compares an
    absolute deadline across it, which is exactly the bug ADR-001 exists
    to prevent."""
    now = SystemClock().now()
    assert now.tzinfo is UTC


async def test_system_clock_sleep_until_a_past_instant_returns_immediately() -> None:
    """Recovery calls this with deadlines that already expired. It must
    not sleep for a negative duration, and it must not raise."""
    clock = SystemClock()
    await clock.sleep_until(clock.now() - timedelta(hours=1))


async def test_fake_clock_does_not_move_on_its_own() -> None:
    clock = FakeClock(T0)
    await asyncio.sleep(0)
    assert clock.now() == T0


async def test_fake_clock_wakes_only_the_sleepers_that_are_due() -> None:
    clock = FakeClock(T0)
    woken: list[str] = []

    async def sleeper(name: str, at: datetime) -> None:
        await clock.sleep_until(at)
        woken.append(name)

    early = asyncio.create_task(sleeper("early", T0 + timedelta(seconds=10)))
    late = asyncio.create_task(sleeper("late", T0 + timedelta(seconds=30)))
    await clock.settle()
    assert clock.pending() == (T0 + timedelta(seconds=10), T0 + timedelta(seconds=30))

    await clock.advance_to(T0 + timedelta(seconds=10))
    assert woken == ["early"]
    assert clock.pending() == (T0 + timedelta(seconds=30),)

    await clock.advance_to(T0 + timedelta(seconds=30))
    assert woken == ["early", "late"]
    await asyncio.gather(early, late)


async def test_fake_clock_sleep_until_the_past_returns_without_registering() -> None:
    clock = FakeClock(T0)
    await clock.sleep_until(T0 - timedelta(seconds=1))
    assert clock.pending() == ()


async def test_fake_clock_advance_to_never_moves_backwards() -> None:
    """A test that rewinds time is a test asserting something that cannot
    happen in production. Fail loudly instead of silently."""
    clock = FakeClock(T0)
    try:
        await clock.advance_to(T0 - timedelta(seconds=1))
    except ValueError:
        return
    raise AssertionError("advance_to must reject a backwards jump")
```

- [ ] **Step 2: Run and watch it fail**

```bash
cd backend && uv run pytest tests/runtime/test_clock.py -q --no-cov
```

Expected: `ModuleNotFoundError: No module named 'triviador.runtime'`.

- [ ] **Step 3: Implement `SystemClock`**

`backend/src/triviador/runtime/__init__.py` is empty. `backend/src/triviador/runtime/clock.py`:

```python
"""The one place the runtime learns what time it is."""

import asyncio
from datetime import UTC, datetime


class SystemClock:
    """`services.ports.Clock` over the real world.

    `sleep_until` clamps a past instant to zero rather than raising: on
    recovery the runtime routinely asks to sleep until a deadline that
    expired while the process was down, and §5.6 requires that case to
    resolve *immediately*, not to be an error the caller must pre-check.
    One path covers both of §5.6's cases — future instant, past instant —
    which is why neither the runtime nor the manager branches on it.
    """

    def now(self) -> datetime:
        return datetime.now(UTC)

    async def sleep_until(self, when: datetime) -> None:
        delay = (when - self.now()).total_seconds()
        await asyncio.sleep(max(0.0, delay))
```

- [ ] **Step 4: Implement the fakes**

`backend/tests/runtime/fakes.py`:

```python
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
from datetime import datetime

from triviador.domain.game.actions import RejectCode
from triviador.domain.game.events import GameEvent
from triviador.domain.game.state import GameState
from triviador.domain.ids import GameId
from triviador.services.ports import RuntimeCode


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
        await event.wait()

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
        assert len(self.resolutions) == 1, f"expected exactly one resolution, got {self.resolutions}"
        return self.resolutions[0]
```

- [ ] **Step 5: Verify**

```bash
cd backend && uv run pytest tests/runtime/test_clock.py -q --no-cov
uv run ruff check . && uv run ruff format --check . && uv run mypy
```

Expected: PASS. `mypy` must be clean — the fakes are type-checked (`files` includes `tests`).

- [ ] **Step 6: Commit**

```bash
git add backend/src/triviador/runtime backend/tests/runtime
git commit -m "feat(runtime): system clock, and the fakes the runtime suite is built on"
```

---

## Task 3: `choose_base_regions` — the non-adjacency rule nothing currently enforces

**Files:**
- Create: `backend/src/triviador/domain/maps/placement.py`
- Test: `backend/tests/domain/maps/test_placement.py`

**Interfaces:**
- Consumes: `domain.maps.definition.MapDefinition` (`region_ids()`, `neighbours(region_id)`).
- Produces: `domain.maps.placement.choose_base_regions(defn, count, rng) -> tuple[RegionId, ...]` and `domain.maps.placement.BasesUnplaceable`. Task 4's materialiser calls it for `StartGame`.

**Why this is domain code and not runtime code.** Spec 1 §3.4 is a *rule*: "`BasesAssigned` requires `player_count` mutually non-adjacent regions", underwritten by the map validator's independent-set assertion. `_decide_start` cannot check it (it does not choose the regions) and the runtime should not own it (it is not a scheduling concern). Taking `rng` as a parameter keeps the function deterministic given its inputs, so domain purity holds: this module imports nothing but `MapDefinition`, `RegionId`, and `random.Random` for the type.

- [ ] **Step 1: Write the failing test**

`backend/tests/domain/maps/test_placement.py`:

```python
"""Spec 1 §3.4. The map validator guarantees an independent set of size ≥ 4
exists for every registered map, so for a valid map this search must always
succeed — a randomized greedy that gives up on an unlucky shuffle would turn
a guaranteed property into a flaky one."""

import random

import pytest
from hypothesis import given
from hypothesis import strategies as st

from tests.conftest import grid_map
from triviador.domain.ids import MapId, RegionId
from triviador.domain.maps.definition import MapDefinition, Region
from triviador.domain.maps.placement import BasesUnplaceable, choose_base_regions


def test_zero_bases_is_an_empty_tuple() -> None:
    assert choose_base_regions(grid_map(), 0, random.Random(0)) == ()


@pytest.mark.parametrize("seed", range(25))
def test_chosen_regions_are_distinct_and_mutually_non_adjacent(seed: int) -> None:
    """The 3x3 grid's only 4-element independent set is the four corners,
    so this also pins the search's completeness: a greedy that took r4
    (the centre) first would be stuck at three and must backtrack."""
    defn = grid_map()
    chosen = choose_base_regions(defn, 4, random.Random(seed))

    assert len(chosen) == 4
    assert len(set(chosen)) == 4
    assert set(chosen) <= set(defn.region_ids())
    for region in chosen:
        assert defn.neighbours(region).isdisjoint(chosen)
    assert set(chosen) == {RegionId("r0"), RegionId("r2"), RegionId("r6"), RegionId("r8")}


def test_different_seeds_produce_different_placements() -> None:
    """Bases must not be predictable across games on the same map — a
    deterministic placement would make the first pick of every expansion
    round known in advance."""
    defn = grid_map()
    seen = {choose_base_regions(defn, 2, random.Random(seed)) for seed in range(30)}
    assert len(seen) > 1


def test_raises_when_no_independent_set_of_that_size_exists() -> None:
    """A complete graph on three regions admits one base, never two. This
    can only be reached by an unregistered or invalid map, so it raises
    rather than returning a short tuple — a short tuple would reach
    `_decide_start` and be rejected there as an *incomplete start context*,
    naming the wrong cause."""
    complete = MapDefinition(
        map_id=MapId("triangle"),
        regions=(Region(RegionId("a"), "A"), Region(RegionId("b"), "B"), Region(RegionId("c"), "C")),
        adjacency={
            RegionId("a"): frozenset({RegionId("b"), RegionId("c")}),
            RegionId("b"): frozenset({RegionId("a"), RegionId("c")}),
            RegionId("c"): frozenset({RegionId("a"), RegionId("b")}),
        },
    )
    with pytest.raises(BasesUnplaceable):
        choose_base_regions(complete, 2, random.Random(0))


@given(seed=st.integers(min_value=0, max_value=10_000), count=st.integers(min_value=1, max_value=4))
def test_property_result_is_always_a_valid_independent_set(seed: int, count: int) -> None:
    defn = grid_map()
    chosen = choose_base_regions(defn, count, random.Random(seed))
    assert len(set(chosen)) == count
    for region in chosen:
        assert defn.neighbours(region).isdisjoint(chosen)
```

- [ ] **Step 2: Run and watch it fail**

```bash
cd backend && uv run pytest tests/domain/maps/test_placement.py -q --no-cov
```

Expected: `ModuleNotFoundError: No module named 'triviador.domain.maps.placement'`.

- [ ] **Step 3: Implement**

`backend/src/triviador/domain/maps/placement.py`:

```python
"""Where a game's bases go.

Spec 1 §3.4: `BasesAssigned` requires `player_count` mutually non-adjacent
regions, and `validate_map` asserts every registered map contains an
independent set of size ≥ 4. `_decide_start` validates only that the
regions it is handed are distinct and on the map — it does not choose
them, so it cannot check adjacency. This module is where the rule is
actually enforced.

Pure by construction: `rng` is a parameter, not a capability. The same
`Random` seed on the same map yields the same placement, which is what
makes the property test above meaningful.
"""

import random

from triviador.domain.ids import RegionId
from triviador.domain.maps.definition import MapDefinition


class BasesUnplaceable(Exception):
    """No independent set of the requested size exists on this map.

    Unreachable for a registered map — `validate_map` refuses to load one
    without an independent set of size ≥ 4, and `player_count` is capped
    at 4. Raised rather than returning a short tuple so the cause is named
    at its source instead of resurfacing as "start context is incomplete".
    """


def choose_base_regions(
    defn: MapDefinition, count: int, rng: random.Random
) -> tuple[RegionId, ...]:
    """`count` mutually non-adjacent regions, chosen uniformly at random
    among the placements reachable from a shuffled scan order.

    Exhaustive backtracking rather than randomized greedy-with-retries:
    the map validator *guarantees* a placement exists, so a search that
    can fail on an unlucky shuffle would convert a guaranteed property
    into an intermittent `StartGame` failure — the worst kind, because it
    would reproduce roughly never. Depth is bounded by `count` (≤ 4) and
    the region list by the map size (tens), so exhaustiveness costs
    nothing measurable.

    Randomness comes from the shuffle: the search takes the first
    placement it reaches in a random scan order, so different seeds land
    on different placements wherever more than one exists.
    """
    if count <= 0:
        return ()

    regions = list(defn.region_ids())
    rng.shuffle(regions)

    chosen: list[RegionId] = []
    blocked: set[RegionId] = set()

    def search(start: int) -> bool:
        if len(chosen) == count:
            return True
        for index in range(start, len(regions)):
            region = regions[index]
            if region in blocked:
                continue
            # Compute the newly blocked set *before* mutating, so the undo
            # below restores exactly what this frame added — subtracting
            # the full neighbour set would unblock regions an outer frame
            # is still relying on.
            newly = (defn.neighbours(region) | {region}) - blocked
            blocked |= newly
            chosen.append(region)
            if search(index + 1):
                return True
            chosen.pop()
            blocked -= newly
        return False

    if not search(0):
        raise BasesUnplaceable(
            f"map {defn.map_id!r} has no independent set of size {count}"
        )
    return tuple(chosen)
```

- [ ] **Step 4: Verify**

```bash
cd backend && uv run pytest tests/domain/maps -q --no-cov
uv run pytest -q
uv run ruff check . && uv run ruff format --check . && uv run mypy
```

Expected: PASS, and the repo-wide run still meets the `reducer.py` / `codec/` coverage gates.

- [ ] **Step 5: Commit**

```bash
git add backend/src/triviador/domain/maps/placement.py backend/tests/domain/maps/test_placement.py
git commit -m "feat(domain): non-adjacent base placement, the rule §3.4 required and nothing enforced"
```

---

## Task 4: The materialiser — every non-deterministic input, resolved inside the transaction

**Files:**
- Create: `backend/src/triviador/runtime/materialiser.py`
- Test: `backend/tests/runtime/test_materialiser.py`, `backend/tests/runtime/conftest.py`

**Interfaces:**
- Consumes: `services.ports.{Clock, Transaction, QuestionPoolUnavailable}` (Task 1), `domain.maps.placement.choose_base_regions` (Task 3), `domain.game.actions.{Command, StartGame, ExpireDeadline, DecisionContext}`, `domain.game.rules.required_question_budget`, `domain.game.state.ExpansionPicking`.
- Produces: `runtime.materialiser.Materialiser(clock, rng)` with `async def build(state, command, tx) -> DecisionContext`. Task 6's executor calls it once per attempt.

**What the domain actually needs, verified against `reducer.py`:** `ctx.now` for every command; `ctx.shuffled_player_ids`, `ctx.base_regions` and `ctx.drawn_pool` for `StartGame` (`_decide_start`); `ctx.shuffled_region_ids` for an `ExpireDeadline` that lands on `ExpansionPicking` (`_decide_auto_pick` falls back to `state.free_regions()` when it is `None`, which would make every auto-pick take the same region in map order). Nothing else in the reducer reads the context.

- [ ] **Step 1: Write the failing test**

`backend/tests/runtime/conftest.py`:

```python
"""Builders shared across the runtime suite.

`lobby_state` and friends live in `tests/conftest.py` and are reused as-is
— the runtime tests assert on runtime behaviour, not on new state shapes.
"""

from datetime import UTC, datetime

import pytest

from tests.runtime.fakes import FakeBroadcaster, FakeClock, FakeSubscribers

T0 = datetime(2026, 8, 17, 12, 0, tzinfo=UTC)


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock(T0)


@pytest.fixture
def broadcaster() -> FakeBroadcaster:
    return FakeBroadcaster()


@pytest.fixture
def subscribers() -> FakeSubscribers:
    return FakeSubscribers()
```

`backend/tests/runtime/test_materialiser.py`:

```python
"""§5.2/§5.3: the materialiser runs inside the command's transaction and
resolves every non-deterministic input into a `DecisionContext`, so
`decide` stays a mathematical function and replay never diverges."""

import random
from collections.abc import Sequence
from datetime import timedelta

import pytest

from tests.conftest import NOW, full_pool, grid_map, lobby_state
from tests.runtime.conftest import T0
from tests.runtime.fakes import FakeClock
from triviador.domain.game.actions import (
    DecisionContext,
    ExpireDeadline,
    JoinGame,
    StartGame,
)
from triviador.domain.game.reducer import decide, fold
from triviador.domain.game.rules import required_question_budget
from triviador.domain.game.state import ExpansionPicking
from triviador.domain.ids import DeadlineId, PlayerId, RegionId
from triviador.domain.questions.types import QuestionBudget, QuestionPool
from triviador.db.errors import InsufficientQuestions, MalformedQuestion  # noqa: F401 — see below
from triviador.runtime.materialiser import Materialiser
from triviador.services.ports import QuestionPoolUnavailable


class StubBank:
    def __init__(self, pool: QuestionPool | None = None, raises: Exception | None = None) -> None:
        self._pool = pool if pool is not None else full_pool()
        self._raises = raises
        self.budgets: list[QuestionBudget] = []

    async def select_pool(self, budget: QuestionBudget) -> QuestionPool:
        self.budgets.append(budget)
        if self._raises is not None:
            raise self._raises
        return self._pool


class StubTransaction:
    """Only `questions` is exercised here — the materialiser never appends."""

    def __init__(self, bank: StubBank) -> None:
        self._bank = bank

    @property
    def questions(self) -> StubBank:
        return self._bank


async def test_now_comes_from_the_clock_for_every_command() -> None:
    clock = FakeClock(T0)
    materialiser = Materialiser(clock=clock, rng=random.Random(0))
    tx = StubTransaction(StubBank())

    ctx = await materialiser.build(lobby_state(), JoinGame(PlayerId("p9"), "P9"), tx)

    assert ctx.now == T0
    assert ctx.drawn_pool is None
    assert ctx.shuffled_player_ids is None
    assert ctx.base_regions is None
    assert ctx.shuffled_region_ids is None


async def test_start_game_draws_the_pool_for_the_rules_budget() -> None:
    bank = StubBank()
    materialiser = Materialiser(clock=FakeClock(T0), rng=random.Random(0))
    state = lobby_state()

    ctx = await materialiser.build(state, StartGame(PlayerId("p1")), StubTransaction(bank))

    assert bank.budgets == [required_question_budget(state.rules)]
    assert ctx.drawn_pool is not None


async def test_start_game_context_satisfies_decide() -> None:
    """The real gate: `_decide_start` rejects an incomplete context, so a
    context this method builds must survive it and produce events."""
    materialiser = Materialiser(clock=FakeClock(T0), rng=random.Random(7))
    state = lobby_state()

    ctx = await materialiser.build(state, StartGame(PlayerId("p1")), StubTransaction(StubBank()))
    events = decide(state, StartGame(PlayerId("p1")), ctx)

    assert events
    assert ctx.shuffled_player_ids is not None
    assert set(ctx.shuffled_player_ids) == set(state.players)


async def test_start_game_bases_are_mutually_non_adjacent() -> None:
    """Spec 1 §3.4, enforced here because `_decide_start` cannot: it
    validates distinctness and membership, never adjacency."""
    materialiser = Materialiser(clock=FakeClock(T0), rng=random.Random(3))
    state = lobby_state()

    ctx = await materialiser.build(state, StartGame(PlayerId("p1")), StubTransaction(StubBank()))

    assert ctx.base_regions is not None
    for region in ctx.base_regions:
        assert state.map.neighbours(region).isdisjoint(ctx.base_regions)


@pytest.mark.parametrize(
    "error",
    [
        InsufficientQuestions(kind=__import__("triviador.domain.questions.types", fromlist=["QuestionKind"]).QuestionKind.NUMERIC, required=17, available=2),
        MalformedQuestion(question_id=__import__("triviador.domain.ids", fromlist=["QuestionId"]).QuestionId("q1"), kind=__import__("triviador.domain.questions.types", fromlist=["QuestionKind"]).QuestionKind.NUMERIC),
    ],
    ids=["insufficient", "malformed"],
)
async def test_a_bank_shortfall_leaves_the_pool_none_and_becomes_a_rejection(
    error: QuestionPoolUnavailable,
) -> None:
    """§5.5: an insufficient bank is an ordinary rejection, not a fault —
    and a malformed row is treated the same, because quarantining a
    healthy lobby over one bad content row turns a fixable data problem
    into a reload loop. The policy is stated once, in `decide`."""
    from triviador.domain.game.actions import RejectCode, RejectedCommand

    materialiser = Materialiser(clock=FakeClock(T0), rng=random.Random(0))
    state = lobby_state()

    ctx = await materialiser.build(state, StartGame(PlayerId("p1")), StubTransaction(StubBank(raises=error)))

    assert ctx.drawn_pool is None
    with pytest.raises(RejectedCommand) as caught:
        decide(state, StartGame(PlayerId("p1")), ctx)
    assert caught.value.code is RejectCode.QUESTION_POOL_INSUFFICIENT


async def test_a_database_failure_in_the_bank_propagates() -> None:
    """Not every bank failure is a rejection. §5.5: an exception in the
    materialiser from the *database* quarantines — only a domain shortfall
    is a rejection, so this one must not be swallowed."""
    materialiser = Materialiser(clock=FakeClock(T0), rng=random.Random(0))

    with pytest.raises(RuntimeError):
        await materialiser.build(
            lobby_state(), StartGame(PlayerId("p1")), StubTransaction(StubBank(raises=RuntimeError("conn lost")))
        )


async def test_expire_deadline_during_picking_shuffles_free_regions() -> None:
    """`_decide_auto_pick` falls back to `state.free_regions()` when
    `shuffled_region_ids` is None — map order, every time, for every
    timed-out pick in every game. The shuffle is what makes an auto-pick
    an arbitrary free region rather than always the lowest-numbered one."""
    materialiser = Materialiser(clock=FakeClock(T0), rng=random.Random(11))
    state = _picking_state()
    assert isinstance(state.turn, ExpansionPicking)

    ctx = await materialiser.build(
        state, ExpireDeadline(state.turn.deadline.id), StubTransaction(StubBank())
    )

    assert ctx.shuffled_region_ids is not None
    assert set(ctx.shuffled_region_ids) == set(state.free_regions())


async def test_expire_deadline_outside_picking_leaves_region_order_none() -> None:
    """Materialise what this command needs, nothing else: a shuffle no
    reducer branch reads is dead weight in every answer window."""
    materialiser = Materialiser(clock=FakeClock(T0), rng=random.Random(0))
    state = _warmup_state()

    ctx = await materialiser.build(
        state, ExpireDeadline(DeadlineId(1)), StubTransaction(StubBank())
    )

    assert ctx.shuffled_region_ids is None
```

Add the two helpers at the bottom of the module — they build real states through the reducer rather than hand-assembling them, so they stay correct as the domain evolves:

```python
def _warmup_state():
    """A started game, parked in its MediaWarmup window."""
    state = lobby_state()
    ctx = DecisionContext(
        now=NOW,
        shuffled_player_ids=tuple(state.players),
        base_regions=(RegionId("r0"), RegionId("r2"), RegionId("r6")),
        drawn_pool=full_pool(),
    )
    return fold(state, decide(state, StartGame(PlayerId("p1")), ctx))


def _picking_state():
    """Drive the warmup and the first expansion question to a timeout, which
    lands on ExpansionPicking with grants to hand out."""
    from tests.conftest import expire_warmup

    state = expire_warmup(_warmup_state())
    assert state.turn is not None
    return fold(
        state,
        decide(
            state,
            ExpireDeadline(state.turn.deadline.id),
            DecisionContext(now=state.turn.deadline.deadline_at + timedelta(seconds=1)),
        ),
    )
```

If `_picking_state` does not land on `ExpansionPicking` on the first try, step it once more through the same `ExpireDeadline` pattern rather than constructing an `ExpansionPicking` by hand — a hand-built turn can hold a combination the reducer never produces.

Replace the `__import__` gymnastics in the `parametrize` block with ordinary module-level imports of `QuestionId` and `QuestionKind`; they are written that way above only to keep the decorator self-contained.

- [ ] **Step 2: Run and watch it fail**

```bash
cd backend && uv run pytest tests/runtime/test_materialiser.py -q --no-cov
```

Expected: `ModuleNotFoundError: No module named 'triviador.runtime.materialiser'`.

- [ ] **Step 3: Implement**

`backend/src/triviador/runtime/materialiser.py`:

```python
"""Command → `DecisionContext`, inside the command's own transaction.

ADR-004 requires `decide` to be a mathematical function: same state, same
command, same context, same events, forever. Everything non-deterministic
— the current instant, a shuffle, a random draw from the question bank —
is resolved *here* and travels into the domain as a value. What the domain
then writes into events is what replay reads back, so a replay can never
observe a different shuffle or a different pool.

Running inside the caller's transaction is not a detail: §5.3 requires the
`FOR SHARE` locks taken by the pool draw to still be held when the
resulting `QuestionPoolDrawn` event is inserted. That is what makes
"fewer than n rows → rejection, game stays in LOBBY" an authoritative
checkpoint rather than an advisory one.
"""

import logging
import random
from dataclasses import dataclass

from triviador.domain.game.actions import (
    Command,
    DecisionContext,
    ExpireDeadline,
    StartGame,
)
from triviador.domain.game.rules import required_question_budget
from triviador.domain.game.state import ExpansionPicking, GameState
from triviador.domain.maps.placement import choose_base_regions
from triviador.domain.questions.types import QuestionPool
from triviador.services.ports import Clock, QuestionPoolUnavailable, Transaction

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Materialiser:
    clock: Clock
    rng: random.Random

    async def build(
        self, state: GameState, command: Command, tx: Transaction
    ) -> DecisionContext:
        now = self.clock.now()

        if isinstance(command, StartGame):
            player_ids = list(state.players)
            self.rng.shuffle(player_ids)
            return DecisionContext(
                now=now,
                shuffled_player_ids=tuple(player_ids),
                base_regions=choose_base_regions(state.map, len(player_ids), self.rng),
                drawn_pool=await self._draw_pool(state, tx),
            )

        if isinstance(command, ExpireDeadline) and isinstance(state.turn, ExpansionPicking):
            # `_decide_auto_pick` falls back to `state.free_regions()` when
            # this is None — i.e. map order, so every timed-out pick in
            # every game would take the lowest-numbered free region.
            free = list(state.free_regions())
            self.rng.shuffle(free)
            return DecisionContext(now=now, shuffled_region_ids=tuple(free))

        return DecisionContext(now=now)

    async def _draw_pool(self, state: GameState, tx: Transaction) -> QuestionPool | None:
        """A bank shortfall is a *domain* shortfall (§5.5): return `None`
        and let `_decide_start` raise `RejectedCommand(
        QUESTION_POOL_INSUFFICIENT)`, so the rejection policy is stated
        once, in the domain, rather than duplicated here.

        `MalformedQuestion` is caught by the same clause deliberately. It
        is bad content, not a broken database, and quarantining on it
        would put a game that is sitting harmlessly in LOBBY into a reload
        loop that ends only when someone edits a row — the same reasoning
        §5.5 uses to keep broadcaster failure out of fault handling. It is
        logged at error, with the offending id, precisely because the
        rejection the player sees names the wrong cause.

        Anything else — a dropped connection, a serialization failure —
        propagates, and the executor's retry or quarantine handles it.
        """
        try:
            return await tx.questions.select_pool(required_question_budget(state.rules))
        except QuestionPoolUnavailable:
            logger.exception("question pool unavailable for game %s", state.game_id)
            return None
```

- [ ] **Step 4: Verify**

```bash
cd backend && uv run pytest tests/runtime -q --no-cov
uv run ruff check . && uv run ruff format --check . && uv run mypy
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/src/triviador/runtime/materialiser.py backend/tests/runtime/
git commit -m "feat(runtime): materialise every non-deterministic input inside the command's transaction"
```

---

## Task 5: Origins — exactly once, non-throwing, idempotent

**Files:**
- Create: `backend/src/triviador/runtime/origins.py`, `backend/src/triviador/runtime/errors.py`
- Test: `backend/tests/runtime/test_origins.py`

**Interfaces:**
- Consumes: `services.ports.{Origin, RuntimeCode}` (Task 1), `domain.game.actions.RejectCode`.
- Produces: `runtime.origins.{CommandOutcome, Accepted, Ignored, Rejected, Failed, SystemOrigin, FutureOrigin}`; `runtime.errors.{RuntimeClosed, ServerBusy, ServerRestarting, GameRecovering, GameUnrecoverable, CommitFault, PermanentReplayFailure}`. Tasks 7–16 raise and resolve these.

**Why the outcome types live here and not in `ports.py`.** `ports.py` declares capabilities the runtime *consumes*; `CommandOutcome` is a value the runtime *produces*. Plan 5's REST and WebSocket handlers will import it from here, which is the direction that already exists (`api → runtime`).

- [ ] **Step 1: Write the failing test**

`backend/tests/runtime/test_origins.py`:

```python
"""§5.2: "Every origin resolves exactly once" and "origin resolution is
non-throwing and idempotent".

The second property is not defensive programming. A REST client can
disconnect while its command sits in the queue, leaving a cancelled future
whose `set_result` raises `InvalidStateError` — *after* the batch has
already committed. If that propagated, a delivery failure on a dead HTTP
request would quarantine a game whose state is durable and correct.
"""

import asyncio

import pytest

from triviador.domain.game.actions import RejectCode
from triviador.domain.game.events import PlayerJoined
from triviador.domain.ids import PlayerId
from triviador.runtime.origins import (
    Accepted,
    Failed,
    FutureOrigin,
    Ignored,
    Rejected,
    SystemOrigin,
)
from triviador.services.ports import RuntimeCode

EVENT = PlayerJoined(PlayerId("p1"), "P1", seat=0)


async def test_future_origin_delivers_the_committed_events() -> None:
    origin = FutureOrigin()
    origin.resolve_ok([EVENT])
    assert await origin.future == Accepted((EVENT,))


async def test_future_origin_delivers_each_outcome_kind() -> None:
    for resolve, expected in (
        (lambda o: o.resolve_noop(), Ignored()),
        (
            lambda o: o.resolve_rejected(RejectCode.GAME_FULL, "lobby is full"),
            Rejected(RejectCode.GAME_FULL, "lobby is full"),
        ),
        (
            lambda o: o.resolve_failed(RuntimeCode.SERVER_BUSY, "queue full"),
            Failed(RuntimeCode.SERVER_BUSY, "queue full"),
        ),
    ):
        origin = FutureOrigin()
        resolve(origin)
        assert await origin.future == expected


async def test_a_second_resolution_is_a_no_op() -> None:
    """Not merely harmless — the first outcome must survive. A runtime bug
    that resolves twice would otherwise silently overwrite a success with
    a shutdown code."""
    origin = FutureOrigin()
    origin.resolve_ok([EVENT])
    origin.resolve_failed(RuntimeCode.SERVER_RESTARTING, "shutting down")

    assert await origin.future == Accepted((EVENT,))


async def test_resolving_a_cancelled_future_does_not_raise() -> None:
    """The regression test §5.2 names: cancel a REST request after its
    command is enqueued, and the command must still commit with the
    runtime healthy. Here that reduces to: this call does not raise."""
    origin = FutureOrigin()
    origin.future.cancel()
    await asyncio.sleep(0)

    origin.resolve_ok([EVENT])  # must not raise


async def test_resolving_from_another_loop_iteration_does_not_raise() -> None:
    """The future may already be done for reasons the runtime never sees.
    Every `resolve_*` swallows and logs its own failure."""
    origin = FutureOrigin()
    origin.future.set_exception(RuntimeError("set out of band"))

    origin.resolve_noop()  # must not raise
    with pytest.raises(RuntimeError):
        await origin.future


def test_system_origin_accepts_every_resolution_silently() -> None:
    """Watchdog, reaper and deadline expiries have nobody waiting. They
    still resolve, because the loop resolves unconditionally and a
    `None` origin would mean a branch on every path."""
    origin = SystemOrigin("watchdog")

    origin.resolve_ok([EVENT])
    origin.resolve_noop()
    origin.resolve_rejected(RejectCode.WRONG_TURN_STATE, "stale")
    origin.resolve_failed(RuntimeCode.GAME_RECOVERING, "quarantined")
```

- [ ] **Step 2: Run and watch it fail**

```bash
cd backend && uv run pytest tests/runtime/test_origins.py -q --no-cov
```

Expected: `ModuleNotFoundError: No module named 'triviador.runtime.origins'`.

- [ ] **Step 3: Implement `runtime/errors.py`**

```python
"""The runtime's own exception surface.

Split from `db/errors.py` on purpose: `runtime/` may not import `db/`, and
these describe scheduling and lifecycle conditions rather than storage
ones. `ServerBusy` and `RuntimeClosed` are raised *out of* `submit`, at
which point the caller still owns the origin; everything else is raised
inside the runtime and never crosses back out.
"""


class RuntimeClosed(Exception):
    """`submit` was called on a runtime that has been quarantined,
    unloaded, or shut down. The caller re-`get()`s the game (§5.6)."""


class ServerBusy(Exception):
    """The command queue is full. `submit` rejects rather than blocking —
    its caller is a WebSocket read loop that must not stall (§5.6)."""


class ServerRestarting(Exception):
    """The manager has stopped accepting new commands (§5.6 shutdown)."""


class GameRecovering(Exception):
    """The registry entry is `Recovering`. Callers see 503 (§5.6)."""


class GameUnrecoverable(Exception):
    """The registry entry is `Failed`: replay will never succeed, so this
    is not retried and is cleared only by operator action (§5.6)."""


class PermanentReplayFailure(Exception):
    """The event log cannot be replayed into a `GameState`, and no amount
    of retrying will change that: an unknown wire type with no upcaster, a
    decode failure, a `map_sha256` mismatch. Sends the registry entry
    straight to `Failed` without backoff, because retrying would only hide
    the incident (§5.6)."""


class CommitFault(Exception):
    """One command attempt failed in a way that quarantines the runtime:
    persistence unavailable after retries, an exception out of
    `decide`/`evolve`, a database error in the materialiser, a
    `ConcurrentModification`, or a reconciliation mismatch (§5.5)."""
```

- [ ] **Step 4: Implement `runtime/origins.py`**

```python
"""Who is waiting for a command's outcome, and how they are told.

Two implementations cover everything: `FutureOrigin` for a caller that
awaits a result (REST, and Plan 5's WebSocket acknowledgements), and
`SystemOrigin` for commands the server issues to itself — deadline
expiries, watchdog re-fires, reaper aborts — where nobody is waiting but
the loop still resolves unconditionally. A nullable origin would mean a
branch on every resolution path in the consumer loop, and the one that got
forgotten would be a hung request.
"""

import asyncio
import logging
from collections.abc import Sequence
from dataclasses import dataclass

from triviador.domain.game.actions import RejectCode
from triviador.domain.game.events import GameEvent
from triviador.services.ports import RuntimeCode

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Accepted:
    events: tuple[GameEvent, ...]


@dataclass(frozen=True)
class Ignored:
    """§6.1's ignore: a stale window, a duplicate, a command the guards
    dropped. Zero events, nothing persisted, nothing broadcast — and
    deliberately *not* an error, because it is a benign race."""


@dataclass(frozen=True)
class Rejected:
    code: RejectCode
    message: str


@dataclass(frozen=True)
class Failed:
    code: RuntimeCode
    message: str


CommandOutcome = Accepted | Ignored | Rejected | Failed


class SystemOrigin:
    """A server-issued command. `label` names the issuer so a rejection
    that should never happen is greppable."""

    def __init__(self, label: str) -> None:
        self._label = label

    def resolve_ok(self, events: Sequence[GameEvent]) -> None:
        return None

    def resolve_noop(self) -> None:
        return None

    def resolve_rejected(self, code: RejectCode, message: str) -> None:
        # A rejection here means the server issued a command the domain
        # refused — a scheduling bug, not a client problem. Nobody is
        # waiting to be told, so the log is the only place it can surface.
        logger.warning("%s command rejected: %s — %s", self._label, code, message)

    def resolve_failed(self, code: RuntimeCode, message: str) -> None:
        logger.warning("%s command failed: %s — %s", self._label, code, message)


class FutureOrigin:
    """Delivers the outcome to an awaiting caller, exactly once.

    `_resolved` rather than `future.done()`: the future can be done for
    reasons this class never caused (a cancelled REST request), and
    "already delivered by me" and "already finished by someone else" are
    different questions. The first outcome wins; a second call is dropped,
    so a runtime bug cannot overwrite a success with a shutdown code.
    """

    def __init__(self) -> None:
        self.future: asyncio.Future[CommandOutcome] = asyncio.get_running_loop().create_future()
        self._resolved = False

    def resolve_ok(self, events: Sequence[GameEvent]) -> None:
        self._deliver(Accepted(tuple(events)))

    def resolve_noop(self) -> None:
        self._deliver(Ignored())

    def resolve_rejected(self, code: RejectCode, message: str) -> None:
        self._deliver(Rejected(code, message))

    def resolve_failed(self, code: RuntimeCode, message: str) -> None:
        self._deliver(Failed(code, message))

    def _deliver(self, outcome: CommandOutcome) -> None:
        if self._resolved:
            return
        self._resolved = True
        try:
            self.future.set_result(outcome)
        except asyncio.InvalidStateError:
            # The awaiting caller vanished — a cancelled HTTP request, most
            # often. §5.2: transport delivery failure is logged and never
            # reaches runtime fault handling. The batch is already durable;
            # destroying a healthy game over a dead socket would be the
            # actual bug.
            logger.debug("origin future already settled; dropping %r", outcome)
```

- [ ] **Step 5: Verify**

```bash
cd backend && uv run pytest tests/runtime -q --no-cov
uv run ruff check . && uv run ruff format --check . && uv run mypy
```

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add backend/src/triviador/runtime/origins.py backend/src/triviador/runtime/errors.py \
        backend/tests/runtime/test_origins.py
git commit -m "feat(runtime): origins that resolve exactly once and never throw"
```

---

## Task 6: `operation_matches` — reconciliation, behind the port

**Files:**
- Modify: `backend/src/triviador/db/unit_of_work.py`, `backend/src/triviador/services/ports.py`
- Test: `backend/tests/db/test_reconciliation.py`

**Interfaces:**
- Consumes: `TransactionContext.events_for_operation` (Plan 3), `db.codec.registry.WIRE_NAMES`.
- Produces: `TransactionContext.operation_matches(game_id, operation_id, *, expected_base_seq, events) -> ReconcileOutcome`, and the same method on `services.ports.Transaction` (added now, deferred from Task 1). Task 7's executor is its only caller.

**Why the comparison lives in `db/`.** §5.5 requires verifying the exact `seq` range, the row count, *and* the ordered types. Types on the wire are wire names, and the event→wire mapping is `db/codec/registry.py`. `runtime/` may not import `db/`, so either the mapping leaks into `services/` — where it would drag the codec into the contract layer — or the comparison moves behind the port next to the query it already owns. The runtime is left with three outcomes and no knowledge of wire names at all.

- [ ] **Step 1: Write the failing test**

`backend/tests/db/test_reconciliation.py`:

```python
"""§5.5's ambiguous-commit reconciliation. "Any mismatch is quarantine,
never 'close enough'" — so each of the four ways a batch can fail to match
gets its own assertion."""

import pytest

from triviador.domain.game.events import PlayerJoined, PlayerLeft
from triviador.domain.ids import PlayerId
from triviador.services.ports import ReconcileOutcome

pytestmark = pytest.mark.integration


async def test_absent_when_nothing_with_that_operation_id_committed(lobby_game) -> None:
    async with lobby_game.uow.begin() as tx:
        verdict = await tx.operation_matches(
            lobby_game.game_id,
            "op-never-ran",
            expected_base_seq=1,
            events=[PlayerJoined(PlayerId("p1"), "P1", seat=0)],
        )
    assert verdict is ReconcileOutcome.ABSENT


async def test_matched_for_the_exact_batch_that_committed(lobby_game) -> None:
    events = [PlayerJoined(PlayerId("p1"), "P1", seat=0), PlayerJoined(PlayerId("p2"), "P2", seat=1)]
    async with lobby_game.uow.begin() as tx:
        await tx.append(lobby_game.game_id, expected_last_seq=1, events=events, operation_id="op-1")

    async with lobby_game.uow.begin() as tx:
        verdict = await tx.operation_matches(
            lobby_game.game_id, "op-1", expected_base_seq=1, events=events
        )
    assert verdict is ReconcileOutcome.MATCHED


async def test_mismatch_when_the_row_count_differs(lobby_game) -> None:
    committed = [PlayerJoined(PlayerId("p1"), "P1", seat=0), PlayerJoined(PlayerId("p2"), "P2", seat=1)]
    async with lobby_game.uow.begin() as tx:
        await tx.append(
            lobby_game.game_id, expected_last_seq=1, events=committed, operation_id="op-1"
        )

    async with lobby_game.uow.begin() as tx:
        verdict = await tx.operation_matches(
            lobby_game.game_id, "op-1", expected_base_seq=1, events=committed[:1]
        )
    assert verdict is ReconcileOutcome.MISMATCH


async def test_mismatch_when_the_ordered_types_differ(lobby_game) -> None:
    """Same count, same seq range, different batch. This is the case a
    bare `SELECT count(*)` would wave through — and the reason
    `events_for_operation` returns wire names rather than just seqs."""
    async with lobby_game.uow.begin() as tx:
        await tx.append(
            lobby_game.game_id,
            expected_last_seq=1,
            events=[PlayerJoined(PlayerId("p1"), "P1", seat=0), PlayerJoined(PlayerId("p2"), "P2", seat=1)],
            operation_id="op-1",
        )

    async with lobby_game.uow.begin() as tx:
        verdict = await tx.operation_matches(
            lobby_game.game_id,
            "op-1",
            expected_base_seq=1,
            events=[PlayerJoined(PlayerId("p1"), "P1", seat=0), PlayerLeft(PlayerId("p2"))],
        )
    assert verdict is ReconcileOutcome.MISMATCH


async def test_mismatch_when_the_seq_range_is_not_the_expected_one(lobby_game) -> None:
    """The batch committed at seq 2, but this attempt decided against
    state.seq = 5. Same rows, wrong place in history — accepting it would
    fold events onto a state they were never decided against."""
    events = [PlayerJoined(PlayerId("p1"), "P1", seat=0)]
    async with lobby_game.uow.begin() as tx:
        await tx.append(lobby_game.game_id, expected_last_seq=1, events=events, operation_id="op-1")

    async with lobby_game.uow.begin() as tx:
        verdict = await tx.operation_matches(
            lobby_game.game_id, "op-1", expected_base_seq=5, events=events
        )
    assert verdict is ReconcileOutcome.MISMATCH
```

`lobby_game` is a fixture that creates one `games` row at `last_seq=1` through `GameRepository.create` and exposes `.game_id` and `.uow`. `tests/db/conftest.py` already builds the pieces (engine, migrated schema, truncation, and the users/maps rows the foreign keys need) — add `lobby_game` there, reusing whatever `tests/db/test_event_store.py` already does to get a game row into place rather than inventing a second way.

- [ ] **Step 2: Run and watch it fail**

```bash
cd backend && docker compose -f docker-compose.test.yml up -d
uv run pytest tests/db/test_reconciliation.py -q --no-cov
```

Expected: `AttributeError: 'TransactionContext' object has no attribute 'operation_matches'`.

- [ ] **Step 3: Implement on `TransactionContext`**

Add to `backend/src/triviador/db/unit_of_work.py` (and import `ReconcileOutcome` from `triviador.services.ports` and `WIRE_NAMES` from `triviador.db.codec.registry`):

```python
    async def operation_matches(
        self,
        game_id: GameId,
        operation_id: str,
        *,
        expected_base_seq: int,
        events: Sequence[GameEvent],
    ) -> ReconcileOutcome:
        """§5.5, verbatim: "Verify the exact expected `seq` range
        (`state.seq + 1 … state.seq + len(events)`), the row count, and the
        ordered types against the batch held in memory."

        Three outcomes, not two. `ABSENT` — zero rows for this
        `operation_id` — means the commit definitively did not land, so
        the caller may safely re-run the whole attempt; collapsing it into
        `MISMATCH` would quarantine a game over a dropped connection that
        cost nothing. `MISMATCH` is never "close enough".

        The comparison lives here rather than in the runtime because it
        needs `WIRE_NAMES`, and `runtime/` may not import `db/`. The
        runtime sees three outcomes and never learns a wire name.
        """
        refs = await self.events_for_operation(game_id, operation_id)
        if not refs:
            return ReconcileOutcome.ABSENT

        expected_seqs = tuple(range(expected_base_seq + 1, expected_base_seq + len(events) + 1))
        expected_types = tuple(WIRE_NAMES[type(event)] for event in events)
        actual_seqs = tuple(ref.seq for ref in refs)
        actual_types = tuple(ref.type for ref in refs)

        if actual_seqs == expected_seqs and actual_types == expected_types:
            return ReconcileOutcome.MATCHED
        return ReconcileOutcome.MISMATCH
```

`events_for_operation` already returns rows ordered by `seq`, so the tuple comparison covers count, order and range together.

- [ ] **Step 4: Add the method to the port**

In `backend/src/triviador/services/ports.py`, add to `class Transaction`, replacing the comment Task 1 left in its place:

```python
    async def operation_matches(
        self,
        game_id: GameId,
        operation_id: str,
        *,
        expected_base_seq: int,
        events: Sequence[GameEvent],
    ) -> ReconcileOutcome: ...
```

Port and adapter land in the same task deliberately: `tests/services/test_ports.py`'s conformance assignment is checked by `mypy` on every commit, so a Protocol method with no implementer is a red build for however long it takes the next task to arrive.

- [ ] **Step 5: Verify**

```bash
cd backend && uv run pytest tests/db -q --no-cov
uv run pytest -q
uv run ruff check . && uv run ruff format --check . && uv run mypy
```

Expected: PASS, including `tests/services/test_ports.py`'s conformance function now that the adapter and the port agree again.

- [ ] **Step 6: Commit**

```bash
git add backend/src/triviador/db/unit_of_work.py backend/src/triviador/services/ports.py \
        backend/tests/db/test_reconciliation.py backend/tests/db/conftest.py
git commit -m "feat(db): reconcile an ambiguous commit by seq range, count, and ordered types"
```

---

## Task 7: The loader — event log to `GameState`, with permanent failure named

**Files:**
- Create: `backend/src/triviador/runtime/loader.py`
- Test: `backend/tests/runtime/test_loader.py`

**Interfaces:**
- Consumes: `services.ports.{UnitOfWorkPort, Transaction, MapProvider}`, `runtime.errors.PermanentReplayFailure` (Task 5), `domain.game.genesis.create_initial_state`, `domain.game.reducer.fold`, `maps.registry.InvalidMapError`.
- Produces: `runtime.loader.GameLoader(uow, maps)` with `async def load(game_id) -> GameState`. Tasks 10–12 call it; the permanent/transient split it draws is what decides `Failed` versus `Recovering`.

**This is where `map_sha256` is finally checked.** Plan 3 stored the digest on `GameCreated` and explicitly deferred verification here, noting it must happen *before* `create_initial_state` — which does not carry the digest onto `GameState` and so leaves recovery nothing to check afterwards. A mismatch means the map file changed under a live game: every region id in the log may now mean a different region. That is unrecoverable by definition.

- [ ] **Step 1: Write the failing test**

`backend/tests/runtime/test_loader.py`:

```python
"""Replay, and the line between "try again later" and "this will never
work". §5.6: transient faults stay `Recovering` with backoff; permanent
ones go straight to `Failed` without retrying, because retrying only hides
the incident."""

from contextlib import asynccontextmanager

import pytest

from tests.conftest import grid_map
from triviador.domain.game.events import GameCreated, PlayerJoined
from triviador.domain.game.rules import DEFAULT_RULES
from triviador.domain.game.state import Phase
from triviador.domain.ids import GameId, MapId, PlayerId
from triviador.maps.registry import InvalidMapError, LoadedMap
from triviador.runtime.errors import PermanentReplayFailure
from triviador.runtime.loader import GameLoader

GOOD_DIGEST = "a" * 64
GAME = GameId("g1")


class StubMaps:
    def __init__(self, digest: str = GOOD_DIGEST, raises: Exception | None = None) -> None:
        self._digest = digest
        self._raises = raises

    def load_with_digest(self, map_id: MapId) -> LoadedMap:
        if self._raises is not None:
            raise self._raises
        return LoadedMap(definition=grid_map(), sha256=self._digest)


class StubUnitOfWork:
    def __init__(self, events=(), raises: Exception | None = None) -> None:
        self._events = tuple(events)
        self._raises = raises

    @asynccontextmanager
    async def begin(self):
        yield self

    async def load_stream(self, game_id: GameId):
        if self._raises is not None:
            raise self._raises
        return self._events


def genesis(digest: str = GOOD_DIGEST) -> GameCreated:
    return GameCreated(
        map_id=MapId("grid"),
        rules=DEFAULT_RULES,
        host_id=PlayerId("p1"),
        map_sha256=digest,
    )


async def test_loads_a_lobby_from_its_genesis_event() -> None:
    loader = GameLoader(uow=StubUnitOfWork([genesis()]), maps=StubMaps())

    state = await loader.load(GAME)

    assert state.phase is Phase.LOBBY
    assert state.seq == 1
    assert state.map.map_id == MapId("grid")


async def test_folds_every_event_after_genesis() -> None:
    loader = GameLoader(
        uow=StubUnitOfWork([genesis(), PlayerJoined(PlayerId("p1"), "P1", seat=0)]), maps=StubMaps()
    )

    state = await loader.load(GAME)

    assert state.seq == 2
    assert PlayerId("p1") in state.players


async def test_a_digest_mismatch_is_permanent() -> None:
    """The map file changed under a live game. Every region id in the log
    may now name a different region, so the log can never be replayed
    into a state that means what it meant when it was written."""
    loader = GameLoader(uow=StubUnitOfWork([genesis("b" * 64)]), maps=StubMaps(GOOD_DIGEST))

    with pytest.raises(PermanentReplayFailure):
        await loader.load(GAME)


async def test_an_invalid_map_is_permanent() -> None:
    loader = GameLoader(
        uow=StubUnitOfWork([genesis()]), maps=StubMaps(raises=InvalidMapError("no map.json"))
    )

    with pytest.raises(PermanentReplayFailure):
        await loader.load(GAME)


async def test_a_transient_map_read_failure_is_not_permanent() -> None:
    """An unmounted volume is not a corrupt map. Wrapping this would mark
    the game `Failed` over a disk hiccup, and `Failed` is cleared only by
    operator action — so a fault that fixed itself in a second would need
    a human to notice it."""
    loader = GameLoader(
        uow=StubUnitOfWork([genesis()]), maps=StubMaps(raises=OSError("input/output error"))
    )

    with pytest.raises(OSError):
        await loader.load(GAME)


async def test_a_log_that_does_not_fold_is_permanent() -> None:
    """`fold` is pure, so this failure is a function of the log and the
    map alone and will reproduce identically forever. Left unwrapped it
    would sit in the recovery backoff loop for the life of the process,
    looking like an outage that might clear.

    Build a stream whose second event is a second `GameCreated` —
    `evolve` raises `GenesisEventNotFoldable` on it.
    """
    loader = GameLoader(uow=StubUnitOfWork([genesis(), genesis()]), maps=StubMaps())

    with pytest.raises(PermanentReplayFailure):
        await loader.load(GAME)


async def test_an_empty_stream_is_permanent() -> None:
    """No genesis event means no `games` row worth loading — replaying an
    empty log will never produce a state, however long we wait."""
    loader = GameLoader(uow=StubUnitOfWork([]), maps=StubMaps())

    with pytest.raises(PermanentReplayFailure):
        await loader.load(GAME)


async def test_a_stream_not_starting_with_genesis_is_permanent() -> None:
    loader = GameLoader(uow=StubUnitOfWork([PlayerJoined(PlayerId("p1"), "P1", seat=0)]), maps=StubMaps())

    with pytest.raises(PermanentReplayFailure):
        await loader.load(GAME)


async def test_a_decode_failure_is_permanent() -> None:
    """An unknown wire type with no upcaster. §5.6 names this exactly:
    permanent, no retry."""
    from triviador.db.errors import UnknownEventType

    loader = GameLoader(uow=StubUnitOfWork(raises=UnknownEventType("battle.unheard_of")), maps=StubMaps())

    with pytest.raises(PermanentReplayFailure):
        await loader.load(GAME)


async def test_a_database_failure_propagates_unchanged() -> None:
    """Transient. It must *not* be wrapped, because wrapping it would send
    the registry entry to `Failed` and stop the retries that would have
    fixed it once the database came back."""
    loader = GameLoader(uow=StubUnitOfWork(raises=OSError("connection refused")), maps=StubMaps())

    with pytest.raises(OSError):
        await loader.load(GAME)
```

The `UnknownEventType` import inside the test is deliberate: the *test* names the concrete adapter error to prove the real one is classified correctly, while `runtime/loader.py` catches only `services.ports.EventStreamCorrupt`, its declared base class (Task 1, Step 4). That pairing is the point — the test would fail the moment the codec's errors stopped subclassing the port's type, which is exactly when the classification would silently break.

- [ ] **Step 2: Run and watch it fail**

```bash
cd backend && uv run pytest tests/runtime/test_loader.py -q --no-cov
```

Expected: `ModuleNotFoundError: No module named 'triviador.runtime.loader'`.

- [ ] **Step 3: Implement**

`backend/src/triviador/runtime/loader.py`:

```python
"""Rebuild a live `GameState` from the durable log.

    create_initial_state(events[0], game_id, map_defn)
    fold(that, events[1:])

is ADR-004 read literally, with the map registry supplying the one
immutable input the log references by id rather than embeds.

This module's real job is the classification. §5.6 splits recovery
failures in two: transient ones retry with backoff and stay `Recovering`;
permanent ones go straight to `Failed` without retrying, because replay
will never succeed and retrying only hides the incident. Getting that
split wrong in either direction is expensive — a permanent failure
retried forever is an invisible outage, and a transient failure marked
`Failed` needs an operator to clear something that would have fixed
itself.
"""

import logging

from triviador.domain.game import events as ev
from triviador.domain.game.genesis import create_initial_state
from triviador.domain.game.reducer import fold
from triviador.domain.game.state import GameState
from triviador.domain.ids import GameId
from triviador.maps.registry import InvalidMapError
from triviador.runtime.errors import PermanentReplayFailure
from triviador.services.ports import EventStreamCorrupt, MapProvider, UnitOfWorkPort

logger = logging.getLogger(__name__)


class GameLoader:
    def __init__(self, uow: UnitOfWorkPort, maps: MapProvider) -> None:
        self._uow = uow
        self._maps = maps

    async def load(self, game_id: GameId) -> GameState:
        try:
            async with self._uow.begin() as tx:
                events = await tx.load_stream(game_id)
        except EventStreamCorrupt as exc:
            # A real type, declared on the port and subclassed by the
            # codec's three decode errors. Matching on class-name strings
            # would silently reclassify any renamed or newly added decode
            # error as transient, and a permanent failure retried forever
            # is an outage with no error to find.
            raise PermanentReplayFailure(
                f"game {game_id}: cannot decode its log — {type(exc).__name__}: {exc}"
            ) from exc
        # Everything else — a dropped connection, a refused socket — is
        # transient and propagates unwrapped, so the manager retries it.

        if not events:
            raise PermanentReplayFailure(f"game {game_id}: empty event stream")

        genesis = events[0]
        if not isinstance(genesis, ev.GameCreated):
            raise PermanentReplayFailure(
                f"game {game_id}: stream starts with {type(genesis).__name__}, not GameCreated"
            )

        # Before `create_initial_state`, not after: that function does not
        # carry the digest onto `GameState`, so a check afterwards would
        # have nothing left to compare (Plan 3, deliberately deferred here).
        try:
            loaded = self._maps.load_with_digest(genesis.map_id)
        except InvalidMapError as exc:
            # `InvalidMapError` only: the map file is missing, malformed,
            # or structurally invalid, and none of that improves by
            # waiting. An `OSError` from the same call — an unmounted
            # volume, a transient read failure — deliberately propagates
            # instead, because marking a game `Failed` for a disk hiccup
            # would need an operator to clear something that fixed itself
            # a second later.
            raise PermanentReplayFailure(
                f"game {game_id}: map {genesis.map_id!r} is invalid — {exc}"
            ) from exc

        if loaded.sha256 != genesis.map_sha256:
            raise PermanentReplayFailure(
                f"game {game_id}: map {genesis.map_id!r} digest is {loaded.sha256}, "
                f"the log was written against {genesis.map_sha256}"
            )

        try:
            return fold(create_initial_state(genesis, game_id, loaded.definition), events[1:])
        except Exception as exc:
            # `create_initial_state` and `fold` are pure, so this failure
            # is a function of the log and the map alone: it will
            # reproduce identically on every retry, forever. Leaving it
            # unwrapped would let a `GenesisEventNotFoldable` or a reducer
            # bug sit in the backoff loop for the life of the process,
            # looking like an outage that might clear.
            raise PermanentReplayFailure(
                f"game {game_id}: its log does not fold — {type(exc).__name__}: {exc}"
            ) from exc
```

- [ ] **Step 4: Verify**

```bash
cd backend && uv run pytest tests/runtime tests/test_layering.py -q --no-cov
uv run ruff check . && uv run ruff format --check . && uv run mypy
```

Expected: PASS, and the layering test still green — `loader.py` names no `db` import.

- [ ] **Step 5: Commit**

```bash
git add backend/src/triviador/runtime/loader.py backend/tests/runtime/test_loader.py
git commit -m "feat(runtime): replay a game from its log, verifying map_sha256 before anything else"
```

---

## Task 8: The command executor — one attempt, its retries, and its reconciliation

**Files:**
- Create: `backend/src/triviador/runtime/commit.py`
- Test: `backend/tests/runtime/test_commit.py`

**Interfaces:**
- Consumes: `services.ports.{UnitOfWorkPort, Transaction, ReconcileOutcome, Clock}`, `runtime.materialiser.Materialiser` (Task 4), `runtime.origins.{Accepted, Ignored, Rejected}` (Task 5), `runtime.errors.CommitFault` (Task 5), `domain.game.reducer.decide`, `domain.game.actions.RejectedCommand`.
- Produces: `runtime.commit.CommandExecutor(uow, materialiser, clock, rng, max_attempts, backoff_base_s)` with `async def execute(state, command, operation_id) -> Accepted | Ignored | Rejected`, raising `CommitFault` on everything §5.5 quarantines. Task 9's consumer loop is its only caller.

**§5.5, as code:**

| Condition | This method does |
|---|---|
| `RejectedCommand` from `decide` | roll back, return `Rejected` |
| zero events | roll back, return `Ignored` |
| `40001` / `40P01` | roll back, re-run **materialiser and `decide`** in a new transaction, up to `max_attempts` |
| failure after `append` returned | ambiguous — reconcile by `(game_id, operation_id)` |
| reconciliation `MATCHED` | return `Accepted` |
| reconciliation `ABSENT` | nothing landed; re-run the attempt |
| reconciliation `MISMATCH` | `CommitFault` |
| anything else | `CommitFault` |

**Retry re-runs the whole attempt, deliberately.** The `FOR SHARE` locks are released at rollback, so reusing an already-materialised `StartGame` pool would mean appending under locks that no longer hold — silently downgrading §10.6's checkpoint back to advisory. Re-running may legitimately produce *different* events (a fresh `ORDER BY random()`, or a rejection if the bank drained meanwhile); nothing was committed, so that is correct. Only `operation_id` is stable across attempts.

- [ ] **Step 1: Write the failing test**

`backend/tests/runtime/test_commit.py`:

```python
"""§5.5's failure table, one test per row."""

import random
from contextlib import asynccontextmanager

import pytest

from tests.conftest import full_pool, lobby_state
from tests.runtime.conftest import T0
from tests.runtime.fakes import FakeClock
from triviador.domain.game.actions import JoinGame, StartGame
from triviador.domain.game.events import PlayerJoined
from triviador.domain.ids import PlayerId
from triviador.domain.questions.types import QuestionBudget, QuestionPool
from triviador.runtime.commit import CommandExecutor
from triviador.runtime.errors import CommitFault
from triviador.runtime.materialiser import Materialiser
from triviador.runtime.origins import Accepted, Ignored, Rejected
from triviador.services.ports import ReconcileOutcome


class FakeSerializationFailure(Exception):
    """Stands in for a wrapped asyncpg error. The executor classifies on
    the SQLSTATE it can reach through `.orig.sqlstate`, exactly as
    SQLAlchemy exposes it, so the fake carries the same shape."""

    def __init__(self, sqlstate: str) -> None:
        super().__init__(sqlstate)
        self.orig = type("Orig", (), {"sqlstate": sqlstate})()


class FakeBank:
    def __init__(self, pool: QuestionPool | None = None) -> None:
        self._pool = pool if pool is not None else full_pool()
        self.draws = 0

    async def select_pool(self, budget: QuestionBudget) -> QuestionPool:
        self.draws += 1
        return self._pool


class FakeTransaction:
    def __init__(self, uow: "FakeUnitOfWork") -> None:
        self._uow = uow

    @property
    def questions(self) -> FakeBank:
        return self._uow.bank

    async def append(self, game_id, *, expected_last_seq, events, operation_id) -> None:
        self._uow.appends.append((expected_last_seq, tuple(events), operation_id))
        if self._uow.append_raises:
            raise self._uow.append_raises.pop(0)

    async def load_stream(self, game_id):
        raise AssertionError("the executor never replays")

    async def events_for_operation(self, game_id, operation_id):
        raise AssertionError("the executor reconciles through operation_matches")

    async def operation_matches(self, game_id, operation_id, *, expected_base_seq, events):
        self._uow.reconciliations += 1
        return self._uow.reconcile_verdict


class FakeUnitOfWork:
    """`exit_raises` is Spec 1 §12.2's "break the commit": the failure
    arrives when the context manager exits, which is where a real COMMIT
    fails."""

    def __init__(self) -> None:
        self.bank = FakeBank()
        self.appends: list[tuple[int, tuple, str]] = []
        self.append_raises: list[Exception] = []
        self.exit_raises: list[Exception] = []
        self.begins = 0
        self.reconciliations = 0
        self.reconcile_verdict = ReconcileOutcome.ABSENT

    @asynccontextmanager
    async def begin(self):
        self.begins += 1
        yield FakeTransaction(self)
        if self.exit_raises:
            raise self.exit_raises.pop(0)


def executor(uow: FakeUnitOfWork, *, max_attempts: int = 3) -> CommandExecutor:
    clock = FakeClock(T0)
    return CommandExecutor(
        uow=uow,
        materialiser=Materialiser(clock=clock, rng=random.Random(0)),
        clock=clock,
        rng=random.Random(0),
        max_attempts=max_attempts,
        backoff_base_s=0.0,
    )


async def test_a_decided_batch_is_appended_and_accepted() -> None:
    uow = FakeUnitOfWork()
    state = lobby_state(players={"p1": 0})

    outcome = await executor(uow).execute(state, JoinGame(PlayerId("p2"), "P2"), "op-1")

    assert isinstance(outcome, Accepted)
    assert [type(e) for e in outcome.events] == [PlayerJoined]
    assert uow.appends[0][0] == state.seq
    assert uow.appends[0][2] == "op-1"


async def test_zero_events_roll_back_and_return_ignored() -> None:
    """§5.2: a no-op resolves before ever reaching `append` — no evolve,
    no reschedule, no publish, and nothing written."""
    uow = FakeUnitOfWork()
    state = _warmup_state()
    from triviador.domain.game.actions import ExpireDeadline
    from triviador.domain.ids import DeadlineId

    outcome = await executor(uow).execute(state, ExpireDeadline(DeadlineId(999)), "op-1")

    assert isinstance(outcome, Ignored)
    assert uow.appends == []


async def test_a_rejected_command_rolls_back_and_reports_the_code() -> None:
    uow = FakeUnitOfWork()
    state = lobby_state(players={"p1": 0, "p2": 1, "p3": 2})

    outcome = await executor(uow).execute(state, JoinGame(PlayerId("p4"), "P4"), "op-1")

    assert isinstance(outcome, Rejected)
    assert outcome.code.value == "game_full"
    assert uow.appends == []


@pytest.mark.parametrize("sqlstate", ["40001", "40P01"])
async def test_a_known_rollback_retries_the_whole_attempt(sqlstate: str) -> None:
    """Not just the append: the materialiser runs again too. The FOR SHARE
    locks were released at rollback, so a reused pool would be appended
    under locks that no longer hold.

    Note where the fake raises: on *exit*, i.e. at COMMIT, with `append`
    already returned. That is the common case in production and it is the
    reason SQLSTATE must be classified before the ambiguity check — the
    `reconciliations == 0` assertion below is what pins that ordering.
    """
    uow = FakeUnitOfWork()
    uow.exit_raises = [FakeSerializationFailure(sqlstate)]
    state = lobby_state()

    outcome = await executor(uow).execute(state, StartGame(PlayerId("p1")), "op-1")

    assert isinstance(outcome, Accepted)
    assert uow.begins == 2
    assert uow.bank.draws == 2       # re-materialised, not reused
    assert uow.reconciliations == 0  # a known rollback is never ambiguous


async def test_retries_are_bounded_and_then_fault() -> None:
    uow = FakeUnitOfWork()
    uow.exit_raises = [FakeSerializationFailure("40001") for _ in range(3)]

    with pytest.raises(CommitFault):
        await executor(uow, max_attempts=3).execute(
            lobby_state(players={"p1": 0}), JoinGame(PlayerId("p2"), "P2"), "op-1"
        )
    assert uow.begins == 3


async def test_a_failure_after_append_reconciles_and_accepts_a_matching_batch() -> None:
    """Spec 1 §12.2's ambiguous commit: drop the connection during COMMIT
    → reconciliation by operation_id, no duplicate batch, no lost batch."""
    uow = FakeUnitOfWork()
    uow.exit_raises = [OSError("connection reset")]
    uow.reconcile_verdict = ReconcileOutcome.MATCHED

    outcome = await executor(uow).execute(
        lobby_state(players={"p1": 0}), JoinGame(PlayerId("p2"), "P2"), "op-1"
    )

    assert isinstance(outcome, Accepted)
    assert uow.reconciliations == 1
    assert len(uow.appends) == 1  # never appended twice


async def test_an_absent_batch_after_an_ambiguous_commit_is_retried() -> None:
    """Nothing landed, so re-running is safe — and better than
    quarantining a game over a connection that dropped for free."""
    uow = FakeUnitOfWork()
    uow.exit_raises = [OSError("connection reset")]
    uow.reconcile_verdict = ReconcileOutcome.ABSENT

    outcome = await executor(uow).execute(
        lobby_state(players={"p1": 0}), JoinGame(PlayerId("p2"), "P2"), "op-1"
    )

    assert isinstance(outcome, Accepted)
    assert uow.begins == 3  # attempt, reconcile, retry


async def test_a_mismatched_batch_faults() -> None:
    """"Any mismatch is quarantine, never close enough.""""
    uow = FakeUnitOfWork()
    uow.exit_raises = [OSError("connection reset")]
    uow.reconcile_verdict = ReconcileOutcome.MISMATCH

    with pytest.raises(CommitFault):
        await executor(uow).execute(
            lobby_state(players={"p1": 0}), JoinGame(PlayerId("p2"), "P2"), "op-1"
        )


async def test_an_unclassified_failure_before_append_faults_without_retrying() -> None:
    """`ConcurrentModification` arrives this way: raised inside `append`
    before it returns, with no SQLSTATE. Retrying it would append events
    decided against state that is no longer current — ADR-002's divergence
    failure, made durable."""
    uow = FakeUnitOfWork()
    uow.append_raises = [RuntimeError("concurrent modification")]

    with pytest.raises(CommitFault):
        await executor(uow).execute(
            lobby_state(players={"p1": 0}), JoinGame(PlayerId("p2"), "P2"), "op-1"
        )
    assert uow.begins == 1
    assert uow.reconciliations == 0


async def test_a_reconciliation_that_cannot_run_faults() -> None:
    uow = FakeUnitOfWork()
    uow.exit_raises = [OSError("connection reset"), OSError("still down")]

    with pytest.raises(CommitFault):
        await executor(uow).execute(
            lobby_state(players={"p1": 0}), JoinGame(PlayerId("p2"), "P2"), "op-1"
        )
```

Reuse `_warmup_state` from `tests/runtime/test_materialiser.py` by moving it into `tests/runtime/conftest.py` as a plain function (not a fixture) when this task needs it, and import it from there in both modules — do not copy it.

- [ ] **Step 2: Run and watch it fail**

```bash
cd backend && uv run pytest tests/runtime/test_commit.py -q --no-cov
```

Expected: `ModuleNotFoundError: No module named 'triviador.runtime.commit'`.

- [ ] **Step 3: Implement**

`backend/src/triviador/runtime/commit.py`:

```python
"""One command, one transaction — and everything §5.5 says to do when that
transaction does not go to plan.

The shape is §5.2's consumer loop with the loop and the origins removed:

    async with uow.begin() as tx:
        ctx    = await materialiser.build(state, command, tx)
        events = decide(state, command, ctx)
        await tx.append(...)
    # COMMIT — every lock released here

Isolating it buys two things. The consumer loop (Task 9) stays a loop you
can read in one screen, and every branch of the failure table can be
tested against a fake unit of work with no queue, no task, and no clock
advancing anywhere.
"""

import asyncio
import logging
import random
from collections.abc import Sequence

from triviador.domain.game.actions import Command, RejectedCommand
from triviador.domain.game.events import GameEvent
from triviador.domain.game.reducer import decide
from triviador.domain.game.state import GameState
from triviador.runtime.errors import CommitFault
from triviador.runtime.materialiser import Materialiser
from triviador.runtime.origins import Accepted, Ignored, Rejected
from triviador.services.ports import Clock, ReconcileOutcome, UnitOfWorkPort

logger = logging.getLogger(__name__)

# §5.5's "known rollback": serialization failure and deadlock detected.
# Both mean the transaction definitively did not commit, which is what
# makes re-running the whole attempt safe.
RETRYABLE_SQLSTATES = frozenset({"40001", "40P01"})


class _NoEvents(Exception):
    """Internal: forces a rollback for a no-op.

    Exiting the `async with` normally would COMMIT an empty transaction —
    harmless, but it holds the connection for a round trip and reads as if
    something was written. §5.5 says rollback; this makes it literal.
    """


def _sqlstate(exc: BaseException) -> str | None:
    """SQLAlchemy wraps the driver error and exposes it as `.orig`;
    asyncpg's `PostgresError` carries `.sqlstate`. Reaching through both
    with `getattr` rather than importing either keeps `runtime/` free of
    the driver — the alternative is a `db` import in the one module that
    most needs to stay portable."""
    orig = getattr(exc, "orig", None)
    sqlstate = getattr(orig, "sqlstate", None)
    return sqlstate if isinstance(sqlstate, str) else None


class CommandExecutor:
    def __init__(
        self,
        *,
        uow: UnitOfWorkPort,
        materialiser: Materialiser,
        clock: Clock,
        rng: random.Random,
        max_attempts: int = 3,
        backoff_base_s: float = 0.05,
    ) -> None:
        self._uow = uow
        self._materialiser = materialiser
        self._clock = clock
        self._rng = rng
        self._max_attempts = max_attempts
        self._backoff_base_s = backoff_base_s

    async def execute(
        self, state: GameState, command: Command, operation_id: str
    ) -> Accepted | Ignored | Rejected:
        for attempt in range(1, self._max_attempts + 1):
            events: tuple[GameEvent, ...] = ()
            appended = False
            try:
                async with self._uow.begin() as tx:
                    ctx = await self._materialiser.build(state, command, tx)
                    events = decide(state, command, ctx)
                    if not events:
                        raise _NoEvents
                    await tx.append(
                        state.game_id,
                        expected_last_seq=state.seq,
                        events=events,
                        operation_id=operation_id,
                    )
                    appended = True
            except _NoEvents:
                return Ignored()
            except RejectedCommand as exc:
                # Raised out of the `async with`, so the transaction rolled
                # back on the way past — including any FOR SHARE locks a
                # StartGame draw had taken. §5.5: state untouched, runtime
                # healthy, reply to the origin.
                return Rejected(exc.code, exc.message)
            except Exception as exc:
                # SQLSTATE first, *before* the ambiguity check — order is
                # load-bearing. A serialization failure or deadlock is
                # reported by PostgreSQL at COMMIT as often as before it,
                # so it routinely arrives with `appended` already true.
                # But it is not ambiguous: 40001 and 40P01 mean the
                # transaction definitively did not commit. Checking
                # `appended` first would send every one of them through a
                # reconciliation round trip that can only ever answer
                # ABSENT — an extra transaction, on the exact path that is
                # already under contention.
                if _sqlstate(exc) in RETRYABLE_SQLSTATES:
                    logger.warning(
                        "game %s: retryable rollback on attempt %d/%d",
                        state.game_id,
                        attempt,
                        self._max_attempts,
                    )
                    await self._backoff(attempt)
                    continue

                if appended:
                    # `append` returned and this is not a known rollback,
                    # so the only operation left was the COMMIT and
                    # whether it landed is unknown. This is the ambiguous
                    # commit, and the *only* path that may look for rows
                    # written by a previous attempt.
                    outcome = await self._reconcile(state, operation_id, events)
                    if outcome is not None:
                        return outcome
                    continue  # ABSENT: nothing landed, re-run the attempt

                raise CommitFault(f"game {state.game_id}: command attempt failed") from exc

            return Accepted(events)

        raise CommitFault(
            f"game {state.game_id}: persistence unavailable after {self._max_attempts} attempts"
        )

    async def _reconcile(
        self, state: GameState, operation_id: str, events: Sequence[GameEvent]
    ) -> Accepted | None:
        """Returns `Accepted` if the batch committed, `None` if it
        definitively did not (the caller re-runs), and raises `CommitFault`
        on a mismatch or if the reconciliation itself cannot run.

        A fresh unit of work: the previous one's connection is exactly
        what failed.
        """
        try:
            async with self._uow.begin() as tx:
                verdict = await tx.operation_matches(
                    state.game_id,
                    operation_id,
                    expected_base_seq=state.seq,
                    events=events,
                )
        except Exception as exc:
            raise CommitFault(
                f"game {state.game_id}: cannot reconcile operation {operation_id}"
            ) from exc

        match verdict:
            case ReconcileOutcome.MATCHED:
                logger.warning(
                    "game %s: ambiguous commit for %s resolved as committed",
                    state.game_id,
                    operation_id,
                )
                return Accepted(tuple(events))
            case ReconcileOutcome.ABSENT:
                logger.warning(
                    "game %s: ambiguous commit for %s did not land; retrying",
                    state.game_id,
                    operation_id,
                )
                return None
            case ReconcileOutcome.MISMATCH:
                raise CommitFault(
                    f"game {state.game_id}: operation {operation_id} committed a batch "
                    f"that is not the one this attempt decided"
                )

    async def _backoff(self, attempt: int) -> None:
        """Full jitter, through the clock rather than `asyncio.sleep`, so
        the fake clock governs it and no test waits on wall-clock time.
        With `backoff_base_s=0` this is a no-op — which is how most tests
        run it."""
        if self._backoff_base_s <= 0:
            return
        delay = self._rng.uniform(0.0, self._backoff_base_s * (2 ** (attempt - 1)))
        await self._clock.sleep_until(
            self._clock.now() + __import__("datetime").timedelta(seconds=delay)
        )
```

Replace that `__import__("datetime")` with a module-level `from datetime import timedelta`; it is inlined above only to keep the method self-contained in the listing.

- [ ] **Step 4: Verify**

```bash
cd backend && uv run pytest tests/runtime -q --no-cov
uv run ruff check . && uv run ruff format --check . && uv run mypy
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/src/triviador/runtime/commit.py backend/tests/runtime/test_commit.py \
        backend/tests/runtime/conftest.py
git commit -m "feat(runtime): the command attempt, its bounded retry, and its reconciliation"
```

---

## Task 9: `GameRuntime` — the queue and the consumer loop

**Files:**
- Create: `backend/src/triviador/runtime/runtime.py`
- Test: `backend/tests/runtime/test_runtime_loop.py`

**Interfaces:**
- Consumes: `runtime.commit.CommandExecutor` (Task 8), `runtime.origins.{Accepted, Ignored, Rejected, SystemOrigin}` (Task 5), `runtime.errors.{RuntimeClosed, ServerBusy, CommitFault}` (Task 5), `services.ports.{Broadcaster, Clock, Origin, RuntimeCode}`, `domain.game.reducer.fold`.
- Produces: `runtime.runtime.{QueuedCommand, GameRuntime}`. `GameRuntime` exposes `state`, `game_id`, `generation`, `closed`, `expiry_enqueued_deadline_id`, `submit(qc)`, `start()`, `drain(code)`, `aclose()`. Tasks 10–16 use all of them.

**§5.2's loop, and the ordering that matters:**

```
qc = await queue.get()                    # nothing open while waiting
outcome = await executor.execute(...)     # one transaction, opened and closed inside
# every lock released before this line
if Ignored:  origin.resolve_noop(); continue      # no evolve, no reschedule, no publish
state = fold(state, events)
reschedule_deadline()
publish()
origin.resolve_ok()
```

**Origin ownership.** An origin belongs to the caller until `submit` returns successfully; from that instant it belongs to the runtime, which resolves it exactly once. So `submit` *raises* `ServerBusy` / `RuntimeClosed` rather than resolving — resolving an origin the runtime never accepted would be a double resolution the moment the caller also handled the raise.

- [ ] **Step 1: Write the failing test**

`backend/tests/runtime/test_runtime_loop.py`:

```python
"""§5.2. The loop is short; the ordering inside it is the whole point."""

import asyncio
import random

import pytest

from tests.conftest import lobby_state
from tests.runtime.conftest import T0
from tests.runtime.fakes import FakeBroadcaster, FakeClock, RecordingOrigin
from triviador.domain.game.actions import ExpireDeadline, JoinGame
from triviador.domain.ids import DeadlineId, PlayerId
from triviador.runtime.errors import RuntimeClosed, ServerBusy
from triviador.runtime.runtime import GameRuntime, QueuedCommand


class StubExecutor:
    """Returns scripted outcomes. The executor's own behaviour is Task 8's
    subject; here it is a boundary.

    Annotated to satisfy `runtime.commit.Executor` structurally — never by
    subclassing it. A stub that drifts from the Protocol then fails
    `mypy --strict` at the call site, which is where it is cheapest to
    notice.
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
    executor = StubExecutor(
        [Rejected(RejectCode.GAME_FULL, "lobby is full"), Ignored()]
    )
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
    runtime = a_runtime(
        StubExecutor([Accepted((event,))]), broadcaster=broadcaster, faults=faults
    )
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
```

Two helpers the module needs, above the tests:

```python
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

    def publish(self, game_id, base_seq, state, events) -> None:
        self._trace.append("publish")
        super().publish(game_id, base_seq, state, events)


class GatedExecutor:
    """Blocks inside `execute` until released — stands in for a COMMIT in
    flight. Shared with `test_shutdown.py` and `test_reaper.py`; put it in
    `tests/runtime/conftest.py`."""

    def __init__(self) -> None:
        self.entered = asyncio.Event()
        self.release = asyncio.Event()

    async def execute(
        self, state: GameState, command: Command, operation_id: str
    ) -> Accepted | Ignored | Rejected:
        self.entered.set()
        await self.release.wait()
        return Accepted((PlayerJoined(PlayerId("p2"), "P2", seat=1),))
```

`consumer_done()` is a one-line accessor on `GameRuntime` — `return self._consumer is not None and self._consumer.done()` — added so a test can assert the loop actually stopped without reaching into a private attribute.

- [ ] **Step 2: Run and watch it fail**

```bash
cd backend && uv run pytest tests/runtime/test_runtime_loop.py -q --no-cov
```

Expected: `ModuleNotFoundError: No module named 'triviador.runtime.runtime'`.

- [ ] **Step 3: Implement**

`backend/src/triviador/runtime/runtime.py`:

```python
"""One game, one queue, one consumer task.

Single-threaded by construction: every mutation of `self._state` happens
in `_consume`, so there is no lock on the state and no window where a
half-applied batch is visible. Everything else on this class either feeds
the queue or reads the current state.
"""

import asyncio
import logging
import random
from collections.abc import Callable
from dataclasses import dataclass
from uuid import uuid4

from triviador.domain.game.actions import Command, ExpireDeadline
from triviador.domain.game.reducer import fold
from triviador.domain.game.state import GameState
from triviador.domain.ids import DeadlineId, GameId
from triviador.runtime.commit import CommandExecutor
from triviador.runtime.errors import CommitFault, RuntimeClosed, ServerBusy
from triviador.runtime.origins import Accepted, Ignored, Rejected, SystemOrigin
from triviador.services.ports import Broadcaster, Clock, Origin, RuntimeCode

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class QueuedCommand:
    command: Command
    operation_id: str
    origin: Origin
    stop: bool = False

    @classmethod
    def stop_sentinel(cls) -> "QueuedCommand":
        """Ends the consumer loop cleanly (Task 16).

        A typed field rather than a sentinel object smuggled through
        `command`: `mypy --strict` would reject the latter, and rightly —
        the loop checks `stop` before it ever looks at `command`.
        """
        return cls(command=AbortGame(actor_id=None), operation_id="", origin=SystemOrigin("shutdown"), stop=True)


class GameRuntime:
    def __init__(
        self,
        *,
        state: GameState,
        executor: Executor,
        clock: Clock,
        broadcaster: Broadcaster,
        on_fault: Callable[["GameRuntime", BaseException], None],
        generation: int,
        rng: random.Random,
        queue_maxsize: int = 256,
    ) -> None:
        self._state = state
        self._executor = executor
        self._clock = clock
        self._broadcaster = broadcaster
        self._on_fault = on_fault
        self.generation = generation
        self._rng = rng
        self._queue: asyncio.Queue[QueuedCommand] = asyncio.Queue(maxsize=queue_maxsize)
        self._consumer: asyncio.Task[None] | None = None
        self._deadline_task: asyncio.Task[None] | None = None
        self._scheduled_deadline_id: DeadlineId | None = None
        self.expiry_enqueued_deadline_id: DeadlineId | None = None
        self._in_flight = False
        self.closed = False

    @property
    def game_id(self) -> GameId:
        return self._state.game_id

    @property
    def state(self) -> GameState:
        return self._state

    @property
    def clock(self) -> Clock:
        """Read-only, and exposed only so tests can drive the fake. The
        runtime itself always goes through `self._clock`."""
        return self._clock

    def pending_commands(self) -> int:
        return self._queue.qsize()

    def is_idle(self) -> bool:
        """Nothing queued *and* nothing in flight.

        `qsize() == 0` alone is a lie: `_consume` removes a command from
        the queue before executing it, so throughout the entire
        transaction — the append, the COMMIT — the queue reads empty
        while a command is very much in progress. A caller that unloaded
        on that reading would cancel the consumer mid-COMMIT, which is
        both the ambiguous-commit case the design goes out of its way
        never to manufacture and an origin nobody ever resolves.

        `_in_flight` is set and cleared by the consumer itself, so it
        cannot disagree with what the consumer is actually doing.
        """
        return self._queue.empty() and not self._in_flight

    def start(self) -> None:
        self._consumer = asyncio.create_task(
            self._consume(), name=f"consume:{self.game_id}:{self.generation}"
        )
        self._reschedule_deadline()

    def submit(self, qc: QueuedCommand) -> None:
        """Synchronous, and it raises rather than resolving.

        An origin belongs to the caller until this returns successfully;
        from that instant it belongs to the runtime, which resolves it
        exactly once. Resolving here would be a double resolution the
        moment the caller also handled the raise.

        `ServerBusy` rather than blocking: the caller is a WebSocket read
        loop that must not stall (§5.6).
        """
        if self.closed:
            raise RuntimeClosed(f"game {self.game_id} generation {self.generation} is closed")
        try:
            self._queue.put_nowait(qc)
        except asyncio.QueueFull as exc:
            raise ServerBusy(f"game {self.game_id} command queue is full") from exc

    def drain(self, code: RuntimeCode, message: str) -> int:
        """Resolve and discard everything queued. Used by quarantine
        (`GAME_RECOVERING`) and shutdown (`SERVER_RESTARTING`) — the two
        places where queued commands will never be processed and their
        origins would otherwise hang forever."""
        drained = 0
        while True:
            try:
                qc = self._queue.get_nowait()
            except asyncio.QueueEmpty:
                return drained
            qc.origin.resolve_failed(code, message)
            drained += 1

    async def _consume(self) -> None:
        while True:
            qc = await self._queue.get()
            if qc.stop:
                return
            # Set *before* the try and cleared in `finally`: the window
            # this closes is the one between dequeuing a command and
            # finishing it, during which `qsize()` reads zero and the
            # reaper would otherwise judge this runtime idle and cancel
            # it mid-transaction.
            self._in_flight = True
            try:
                await self._apply(qc)
            except CommitFault as exc:
                # Resolve *this* origin before handing off: quarantine
                # drains the queue, and a command already dequeued is not
                # in it. Then report to the manager and stop consuming —
                # teardown must not run on this task, because a task
                # cannot cancel and await itself (§5.6).
                qc.origin.resolve_failed(RuntimeCode.GAME_RECOVERING, str(exc))
                logger.error("game %s: quarantining — %s", self.game_id, exc)
                self._on_fault(self, exc)
                return
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # pragma: no cover — belt and braces
                qc.origin.resolve_failed(RuntimeCode.GAME_RECOVERING, str(exc))
                logger.exception("game %s: unexpected consumer failure", self.game_id)
                self._on_fault(self, exc)
                return
            finally:
                self._in_flight = False

    async def _apply(self, qc: QueuedCommand) -> None:
        base_seq = self._state.seq
        outcome = await self._executor.execute(self._state, qc.command, qc.operation_id)
        # Past this line every database lock is released: `execute` opened
        # and closed its own transaction. §5.2 — no external response is
        # produced while locks are held.

        match outcome:
            case Ignored():
                qc.origin.resolve_noop()
                return
            case Rejected(code=code, message=message):
                qc.origin.resolve_rejected(code, message)
                return
            case Accepted(events=events):
                self._state = fold(self._state, events)
                self._reschedule_deadline()
                self._publish(base_seq, events)
                qc.origin.resolve_ok(events)

    def _publish(self, base_seq: int, events: Sequence[GameEvent]) -> None:
        """§5.5: a broadcaster failure is logged and never quarantines.
        The commit is durable and memory is correct; §8.5 already gives
        every client an unconditional recovery path."""
        try:
            self._broadcaster.publish(self.game_id, base_seq, self._state, events)
        except Exception:
            logger.exception("game %s: broadcast failed after commit", self.game_id)

    def _reschedule_deadline(self) -> None:
        """Task 10 implements this. Defined here as a no-op so the loop is
        complete and testable on its own."""
        return None

    async def stop(self) -> None:
        """End the consumer loop *without* cancelling it.

        The sentinel goes in after `closed` is set, so nothing can be
        submitted behind it, and the consumer picks it up only once it has
        finished whatever it was doing. That is the whole point:
        cancelling a consumer mid-COMMIT would manufacture the
        ambiguous-commit case — on every deploy for shutdown (Task 16),
        and on an unlucky tick for the reaper (Task 15).

        The deadline task *is* cancelled: it holds no transaction, and a
        timer firing into a queue nobody will read again is noise.
        """
        self.closed = True
        if self._deadline_task is not None:
            self._deadline_task.cancel()
        if self._consumer is not None:
            self._queue.put_nowait(QueuedCommand.stop_sentinel())
            await self._consumer
            self._consumer = None
        if self._deadline_task is not None:
            try:
                await self._deadline_task
            except asyncio.CancelledError:
                pass
            self._deadline_task = None

    async def aclose(self) -> None:
        """The ungraceful counterpart, for quarantine only.

        Quarantine is reached because something already broke, and the
        in-flight transaction is usually the thing that broke — waiting
        for it politely could mean waiting on a dead connection's
        timeout. Everywhere else, use `stop()`.
        """
        self.closed = True
        for task in (self._deadline_task, self._consumer):
            if task is not None:
                task.cancel()
        for task in (self._deadline_task, self._consumer):
            if task is not None:
                try:
                    await task
                except asyncio.CancelledError:
                    pass
        self._deadline_task = None
        self._consumer = None
```

Two typing notes for this module:

- `executor` is typed as the `Executor` Protocol, not as `CommandExecutor`. Add it to `runtime/commit.py` and import it here:

  ```python
  class Executor(Protocol):
      async def execute(
          self, state: GameState, command: Command, operation_id: str
      ) -> Accepted | Ignored | Rejected: ...
  ```

  `CommandExecutor` satisfies it structurally. Without this, every test that passes a stub executor fails `mypy --strict`, and the fix at that point is invariably to weaken the annotation.

- Import `Sequence` from `collections.abc`, and `AbortGame` alongside `ExpireDeadline` (the stop sentinel needs a `Command` value to fill the field it never reads).

- [ ] **Step 4: Verify**

```bash
cd backend && uv run pytest tests/runtime -q --no-cov
uv run ruff check . && uv run ruff format --check . && uv run mypy
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/src/triviador/runtime/runtime.py backend/tests/runtime/test_runtime_loop.py
git commit -m "feat(runtime): the per-game queue and consumer loop"
```

---

## Task 10: Deadlines — one-shot task, respawned when the `DeadlineId` changes

**Files:**
- Modify: `backend/src/triviador/runtime/runtime.py` (`_reschedule_deadline`, the deadline task)
- Test: `backend/tests/runtime/test_deadlines.py`

**Interfaces:**
- Consumes: `Clock.sleep_until` (Task 2), `GameState.current_deadline()`, `runtime.origins.SystemOrigin` (Task 5).
- Produces: a `GameRuntime` whose deadline task is respawned whenever `current_deadline().id` changes, and whose `expiry_enqueued_deadline_id` is set the moment an expiry is queued. Task 14's watchdog fences on that field.

**§5.4, and why a stale fire is harmless.** The task is one-shot: it sleeps until an absolute instant and submits `ExpireDeadline(deadline_id)`. Cancellation is best-effort — if a cancelled task fires anyway, guard 2 in `decide` drops it (`current.id != command.deadline_id` → zero events), so correctness never depends on cancellation winning a race. That is also why recovery needs no special case: `sleep_until` on a past instant returns immediately and the expiry is enqueued at once, which is §5.6's "if it has already passed, `ExpireDeadline` is enqueued immediately".

- [ ] **Step 1: Write the failing test**

`backend/tests/runtime/test_deadlines.py`:

```python
"""§5.4 and §5.6's recovery clause. Every instant here is absolute and
every advance is explicit — this file would be the easiest place in the
suite to accidentally test the event loop's scheduler instead of the
runtime."""

import random
from datetime import timedelta

import pytest

from tests.conftest import lobby_state
from tests.runtime.conftest import T0, warmup_state
from tests.runtime.fakes import FakeBroadcaster, FakeClock, RecordingOrigin
from triviador.domain.game.actions import ExpireDeadline
from triviador.runtime.runtime import GameRuntime, QueuedCommand


class CapturingExecutor:
    """Records what the loop hands it and commits nothing."""

    def __init__(self) -> None:
        self.commands: list[object] = []

    async def execute(self, state, command, operation_id):
        from triviador.runtime.origins import Ignored

        self.commands.append(command)
        return Ignored()


def a_runtime(state, clock, executor):
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
    second = runtime.state.current_deadline()
    assert second is not None
    await clock.advance_to(second.deadline_at)

    operation_ids = [call[2] for call in executor.calls]
    assert all(operation_ids)                      # never empty
    assert len(set(operation_ids)) == len(operation_ids)  # never reused
    await runtime.aclose()
```

`a_runtime` here takes `(state, clock, executor)` — a different shape from Task 9's, because these tests vary the state and the clock rather than the broadcaster. Keep both local to their modules rather than forcing one signature to serve both.

`settle` is the narrowing helper from `tests/runtime/conftest.py`:

```python
async def settle(runtime: GameRuntime) -> None:
    """`GameRuntime.clock` is typed as the `Clock` Protocol, which has no
    `settle`. Narrow here once instead of casting in every test."""
    clock = runtime.clock
    assert isinstance(clock, FakeClock)
    await clock.settle()
```

Move `warmup_state` from `tests/runtime/test_materialiser.py` into `tests/runtime/conftest.py` as a plain function if Task 8 has not already done so.

- [ ] **Step 2: Run and watch it fail**

```bash
cd backend && uv run pytest tests/runtime/test_deadlines.py -q --no-cov
```

Expected: `assert clock.pending() == (deadline.deadline_at,)` fails with `()` — `_reschedule_deadline` is still Task 9's no-op.

- [ ] **Step 3: Implement**

Replace `_reschedule_deadline` in `backend/src/triviador/runtime/runtime.py` and add the task body:

```python
    def _reschedule_deadline(self) -> None:
        """One-shot task, cancelled and respawned whenever
        `current_deadline().id` changes (§5.4).

        Keyed on the id, not on the instant: two different windows can
        share a `deadline_at` down to the microsecond, and re-arming on
        every command would reset the sleep each time a player answered —
        quietly extending the window they are racing.
        """
        deadline = self._state.current_deadline()
        target = deadline.id if deadline is not None else None
        if target == self._scheduled_deadline_id:
            return

        if self._deadline_task is not None:
            self._deadline_task.cancel()
            self._deadline_task = None
        self._scheduled_deadline_id = target

        if deadline is None:
            return

        self._deadline_task = asyncio.create_task(
            self._await_deadline(deadline.id, deadline.deadline_at),
            name=f"deadline:{self.game_id}:{deadline.id}",
        )

    async def _await_deadline(self, deadline_id: DeadlineId, when: datetime) -> None:
        """Sleeps until an absolute instant and submits one expiry.

        `sleep_until` on a past instant returns immediately, which is
        exactly §5.6's recovery clause — a window that expired while the
        process was down is expired *now*, not restarted. One code path
        covers both of that clause's cases.

        A stale fire is harmless under guard 2 (`current.id !=
        command.deadline_id` → zero events), so correctness never depends
        on cancellation winning the race against a wake-up.
        """
        try:
            await self._clock.sleep_until(when)
        except asyncio.CancelledError:
            return

        previous_fence = self.expiry_enqueued_deadline_id
        self.expiry_enqueued_deadline_id = deadline_id
        try:
            self.submit(
                QueuedCommand(
                    command=ExpireDeadline(deadline_id),
                    operation_id=f"deadline-{self.game_id}-{deadline_id}-{uuid4()}",
                    origin=SystemOrigin("deadline"),
                )
            )
        except (RuntimeClosed, ServerBusy):
            # The fence must be *rolled back* when the enqueue fails.
            # Setting it first closes the window in which a watchdog tick
            # sees a queued expiry with no fence and enqueues a second —
            # but leaving it set after a failure is far worse: nothing is
            # in the queue, and every later tick now skips this deadline
            # because the fence says an expiry is already pending. The
            # game would stall on that window forever, with the watchdog
            # that exists to rescue it looking straight past it.
            #
            # Closed: the manager is tearing this runtime down and the new
            # generation will re-arm from the rebuilt state. Busy: 256
            # commands are queued, and the watchdog must stay free to
            # re-fire once the queue drains.
            self.expiry_enqueued_deadline_id = previous_fence
            logger.warning("game %s: could not enqueue expiry for %s", self.game_id, deadline_id)
```

Import `datetime` from `datetime` and `uuid4` from `uuid` if Task 9 did not already.

Set `self.expiry_enqueued_deadline_id` *before* `submit`, not after: the watchdog's fence must be visible the instant the command is in the queue, and a fence set afterwards leaves a window in which a tick sees a queued expiry with no fence and enqueues a second one.

- [ ] **Step 4: Verify**

```bash
cd backend && uv run pytest tests/runtime -q --no-cov
uv run ruff check . && uv run ruff format --check . && uv run mypy
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/src/triviador/runtime/runtime.py backend/tests/runtime/test_deadlines.py \
        backend/tests/runtime/conftest.py
git commit -m "feat(runtime): absolute deadlines, respawned on change and honoured across a restart"
```

---

## Task 11: `GameManager` — the registry, and loading a game exactly once

**Files:**
- Create: `backend/src/triviador/runtime/manager.py`
- Test: `backend/tests/runtime/test_manager.py`

**Interfaces:**
- Consumes: `runtime.loader.GameLoader` (Task 7), `runtime.commit.CommandExecutor` (Task 8), `runtime.runtime.GameRuntime` (Task 9), `runtime.errors.{GameRecovering, GameUnrecoverable, PermanentReplayFailure}` (Task 5), `services.ports.{Clock, Broadcaster, GameSubscriberControl, GameQueriesPort}`.
- Produces: `runtime.manager.{Entry, Live, Recovering, Failed, GameManager}`. `GameManager` exposes `async get(game_id) -> GameRuntime`, `live_runtimes() -> tuple[GameRuntime, ...]`, `degraded() -> tuple[tuple[GameId, str], ...]`, and `entry_for(game_id) -> Entry | None`. Tasks 12–17 build on it.

**Load-once (§5.6).** `get` hits the dict; on a miss it takes a *per-game* `asyncio.Lock`, re-checks, then loads. Without the lock two concurrent joins build two runtimes for one game — ADR-002's divergence failure, in-process, with both runtimes appending at the same `expected_last_seq` and one of them quarantining on `ConcurrentModification` after every command.

**Three registry states, not two.** Quarantine is reached *because* something broke, most often persistence, so "immediately load a fresh generation" is the least likely operation to succeed at that moment. `Recovering` (503 `GAME_RECOVERING`) and `Failed` (503 `GAME_UNRECOVERABLE`, operator-visible) are what let the registry answer honestly while that plays out.

- [ ] **Step 1: Write the failing test**

`backend/tests/runtime/test_manager.py`:

```python
"""§5.6's registry. Load-once, three states, and generations that never
mix."""

import asyncio
import random

import pytest

from tests.conftest import lobby_state
from tests.runtime.conftest import T0
from tests.runtime.fakes import FakeBroadcaster, FakeClock, FakeSubscribers
from triviador.domain.ids import GameId
from triviador.runtime.errors import GameRecovering, GameUnrecoverable, PermanentReplayFailure
from triviador.runtime.manager import Failed, GameManager, Live, Recovering

GAME = GameId("g1")


class CountingLoader:
    def __init__(self, raises: Exception | None = None) -> None:
        self.calls = 0
        self._raises = raises

    async def load(self, game_id: GameId):
        self.calls += 1
        if self._raises is not None:
            raise self._raises
        await asyncio.sleep(0)  # a real load awaits the database
        return lobby_state()


def a_manager(loader, **overrides) -> GameManager:
    return GameManager(
        loader=loader,
        uow=object(),
        materialiser=object(),
        clock=FakeClock(T0),
        broadcaster=FakeBroadcaster(),
        subscribers=FakeSubscribers(),
        games=object(),
        rng=random.Random(0),
        **overrides,
    )


async def test_get_loads_once_and_caches() -> None:
    loader = CountingLoader()
    manager = a_manager(loader)

    first = await manager.get(GAME)
    second = await manager.get(GAME)

    assert first is second
    assert loader.calls == 1


async def test_concurrent_gets_build_one_runtime() -> None:
    """Without the per-game lock this is ADR-002's divergence failure
    in-process: two runtimes for one game, both appending at the same
    expected_last_seq."""
    loader = CountingLoader()
    manager = a_manager(loader)

    runtimes = await asyncio.gather(*(manager.get(GAME) for _ in range(8)))

    assert len({id(r) for r in runtimes}) == 1
    assert loader.calls == 1


async def test_each_load_takes_the_next_generation() -> None:
    manager = a_manager(CountingLoader())

    first = await manager.get(GAME)
    second = await manager.get(GameId("g2"))

    assert second.generation > first.generation


async def test_a_recovering_entry_refuses_callers() -> None:
    manager = a_manager(CountingLoader())
    manager._entries[GAME] = Recovering(attempt=2, next_at=T0)

    with pytest.raises(GameRecovering):
        await manager.get(GAME)


async def test_a_failed_entry_refuses_callers_and_is_operator_visible() -> None:
    manager = a_manager(CountingLoader())
    manager._entries[GAME] = Failed(reason="map digest mismatch")

    with pytest.raises(GameUnrecoverable):
        await manager.get(GAME)
    assert manager.degraded() == ((GAME, "map digest mismatch"),)


async def test_a_permanent_load_failure_goes_straight_to_failed() -> None:
    """No backoff, no retry: replay will never succeed, and retrying only
    hides the incident (§5.6)."""
    manager = a_manager(CountingLoader(raises=PermanentReplayFailure("bad digest")))

    with pytest.raises(GameUnrecoverable):
        await manager.get(GAME)
    assert isinstance(manager.entry_for(GAME), Failed)


async def test_a_transient_load_failure_leaves_no_entry_behind() -> None:
    """A database blip on a first `get` is not a quarantine — there is no
    runtime to tear down and nothing to recover. The caller sees the
    error and the next `get` tries again from scratch."""
    manager = a_manager(CountingLoader(raises=OSError("connection refused")))

    with pytest.raises(OSError):
        await manager.get(GAME)
    assert manager.entry_for(GAME) is None


async def test_live_runtimes_lists_only_live_entries() -> None:
    manager = a_manager(CountingLoader())
    await manager.get(GAME)
    manager._entries[GameId("g2")] = Recovering(attempt=1, next_at=T0)

    assert [r.game_id for r in manager.live_runtimes()] == [GAME]


async def test_get_reloads_a_closed_runtime() -> None:
    """§5.6's generation fencing from the caller's side: a runtime that
    was closed out from under a caller is not handed out again."""
    loader = CountingLoader()
    manager = a_manager(loader)
    first = await manager.get(GAME)
    await first.aclose()

    second = await manager.get(GAME)

    assert second is not first
    assert loader.calls == 2
```

Two notes on `a_manager`:

- Put it in `tests/runtime/conftest.py`, not in this module. Tasks 12, 13 and 15 all call it, with `clock=`, `games=`, `backoff_initial_s=` and `backoff_max_s=` overrides.
- It must not pass `object()` for the collaborators the registry tests never reach — `mypy --strict` will reject that against the port-typed parameters. Give the module three trivial stubs instead: `class _NoUnitOfWork` whose `begin` raises `AssertionError("not reached in registry tests")`, and the same shape for the materialiser and the games queries. A stub that raises documents the boundary; `object()` only postpones the error to runtime.

`entry_for` returns `Entry | None`, so any test that reaches for `.runtime` must narrow first — `entry = manager.entry_for(GAME); assert isinstance(entry, Live)` — rather than chaining off the call.

- [ ] **Step 2: Run and watch it fail**

```bash
cd backend && uv run pytest tests/runtime/test_manager.py -q --no-cov
```

Expected: `ModuleNotFoundError: No module named 'triviador.runtime.manager'`.

- [ ] **Step 3: Implement**

`backend/src/triviador/runtime/manager.py`:

```python
"""Every runtime in the process, and the two background tasks.

`GameRuntime` owns exactly one game; `GameManager` owns the dict of them,
the per-game locks that keep it to one runtime per game, the generation
counter, and — from Task 12 — quarantine and recovery.
"""

import asyncio
import itertools
import logging
import random
from dataclasses import dataclass
from datetime import datetime

from triviador.domain.ids import GameId
from triviador.runtime.commit import CommandExecutor
from triviador.runtime.errors import (
    GameRecovering,
    GameUnrecoverable,
    PermanentReplayFailure,
    ServerRestarting,
)
from triviador.runtime.loader import GameLoader
from triviador.runtime.materialiser import Materialiser
from triviador.runtime.runtime import GameRuntime
from triviador.services.ports import (
    Broadcaster,
    Clock,
    GameQueriesPort,
    GameSubscriberControl,
    RuntimeCode,
    UnitOfWorkPort,
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Live:
    runtime: GameRuntime


@dataclass(frozen=True)
class Recovering:
    """Callers get `GAME_RECOVERING` (503). Transient faults stay here
    while the backoff runs."""

    attempt: int
    next_at: datetime


@dataclass(frozen=True)
class Failed:
    """Callers get `GAME_UNRECOVERABLE` (503). Logged at error, surfaced
    in `/api/health/ready` as a degraded detail, and cleared only by
    operator action."""

    reason: str


Entry = Live | Recovering | Failed


class GameManager:
    def __init__(
        self,
        *,
        loader: GameLoader,
        uow: UnitOfWorkPort,
        materialiser: Materialiser,
        clock: Clock,
        broadcaster: Broadcaster,
        subscribers: GameSubscriberControl,
        games: GameQueriesPort,
        rng: random.Random,
        queue_maxsize: int = 256,
        commit_max_attempts: int = 3,
    ) -> None:
        self._loader = loader
        self._uow = uow
        self._materialiser = materialiser
        self._clock = clock
        self._broadcaster = broadcaster
        self._subscribers = subscribers
        self._games = games
        self._rng = rng
        self._queue_maxsize = queue_maxsize
        self._commit_max_attempts = commit_max_attempts
        self._entries: dict[GameId, Entry] = {}
        self._locks: dict[GameId, asyncio.Lock] = {}
        self._generations = itertools.count(1)
        # Set by Task 16's `shutdown`. Declared here because `_load`
        # already reads it — a flag added later to a method written
        # earlier is how the shutdown race got in.
        self._shutting_down = False

    def entry_for(self, game_id: GameId) -> Entry | None:
        return self._entries.get(game_id)

    def live_runtimes(self) -> tuple[GameRuntime, ...]:
        return tuple(e.runtime for e in self._entries.values() if isinstance(e, Live))

    def degraded(self) -> tuple[tuple[GameId, str], ...]:
        """What `/api/health/ready` reports (Plan 5 consumes this)."""
        return tuple(
            (gid, e.reason) for gid, e in self._entries.items() if isinstance(e, Failed)
        )

    async def get(self, game_id: GameId) -> GameRuntime:
        entry = self._entries.get(game_id)
        runtime = self._usable(entry)
        if runtime is not None:
            return runtime

        # The lock is per game, not global: loading one game must not
        # serialize every other game's first join.
        lock = self._locks.setdefault(game_id, asyncio.Lock())
        async with lock:
            # Re-check under the lock. Without this, every waiter that
            # queued behind the first loader would load again.
            runtime = self._usable(self._entries.get(game_id))
            if runtime is not None:
                return runtime
            return await self._load(game_id)

    def _usable(self, entry: Entry | None) -> GameRuntime | None:
        match entry:
            case Live(runtime=runtime) if not runtime.closed:
                return runtime
            case Recovering():
                raise GameRecovering("game is recovering")
            case Failed(reason=reason):
                raise GameUnrecoverable(reason)
            case _:
                return None

    async def _load(self, game_id: GameId) -> GameRuntime:
        # Task 16 adds this guard. It is the one that actually holds: a
        # recovery already inside `_load` when the fence goes up would
        # otherwise install a `Live` runtime after `shutdown()` returned,
        # and the checks in `_recover` never get another turn to notice.
        if self._shutting_down:
            raise ServerRestarting("server is restarting")

        try:
            state = await self._loader.load(game_id)
        except PermanentReplayFailure as exc:
            # Straight to Failed, no backoff: retrying a log that cannot
            # be decoded only hides the incident.
            logger.error("game %s: unrecoverable — %s", game_id, exc)
            self._entries[game_id] = Failed(reason=str(exc))
            raise GameUnrecoverable(str(exc)) from exc

        runtime = GameRuntime(
            state=state,
            executor=CommandExecutor(
                uow=self._uow,
                materialiser=self._materialiser,
                clock=self._clock,
                rng=self._rng,
                max_attempts=self._commit_max_attempts,
            ),
            clock=self._clock,
            broadcaster=self._broadcaster,
            on_fault=self._on_fault,
            generation=next(self._generations),
            rng=self._rng,
            queue_maxsize=self._queue_maxsize,
        )
        runtime.start()
        self._entries[game_id] = Live(runtime)
        return runtime

    def _on_fault(self, runtime: GameRuntime, exc: BaseException) -> None:
        """Task 12 replaces this with the scheduled quarantine. Until then
        it only logs, so Task 11's registry can be tested on its own."""
        logger.error("game %s: fault reported — %s", runtime.game_id, exc)
```

A transient load failure deliberately writes no entry: there is no runtime to tear down and nothing queued, so the caller simply sees the error and the next `get` tries again. `Recovering` is for a game that *had* a runtime — that is Task 12.

- [ ] **Step 4: Verify**

```bash
cd backend && uv run pytest tests/runtime -q --no-cov
uv run ruff check . && uv run ruff format --check . && uv run mypy
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/src/triviador/runtime/manager.py backend/tests/runtime/test_manager.py
git commit -m "feat(runtime): the game registry, load-once, and its three states"
```

---

## Task 12: Quarantine and recovery with backoff

**Files:**
- Modify: `backend/src/triviador/runtime/manager.py`
- Test: `backend/tests/runtime/test_quarantine.py`

**Interfaces:**
- Consumes: everything Task 11 produced, plus `GameRuntime.{drain, aclose, closed}` (Task 9) and `GameSubscriberControl.close_game_subscribers` (Task 1).
- Produces: `GameManager._on_fault` scheduling `_quarantine`, and `GameManager.quarantine(runtime, reason)` for tests and for Plan 5's operator endpoint. No new public names beyond `quarantine`.

**§5.6's teardown, in order, under the per-game lock:**

```
detach from the registry
mark closed
drain the queue, resolving every origin with GAME_RECOVERING
cancel the consumer and deadline tasks
close_game_subscribers(game_id, 1011)     ← via the port; sockets stay owned by the hub
load a fresh generation
```

**It never runs on the faulting task.** `_on_fault` is called from inside the consumer loop; it only schedules. A task that cancelled and awaited itself would hang forever.

- [ ] **Step 1: Write the failing test**

`backend/tests/runtime/test_quarantine.py`:

```python
"""§5.6. The hard part is not the teardown — it is that recovery can fail
too, and that commands queued against R17 must never surface in R18."""

import asyncio
import random
from datetime import timedelta

import pytest

from tests.conftest import lobby_state
from tests.runtime.conftest import T0
from tests.runtime.fakes import FakeBroadcaster, FakeClock, FakeSubscribers, RecordingOrigin
from triviador.domain.game.actions import JoinGame
from triviador.domain.ids import GameId, PlayerId
from triviador.runtime.errors import CommitFault, GameRecovering, PermanentReplayFailure
from triviador.runtime.manager import Failed, Live, Recovering
from triviador.runtime.runtime import QueuedCommand
from triviador.services.ports import RuntimeCode

GAME = GameId("g1")


class ScriptedLoader:
    """`outcomes` is consumed one per load: a `GameState`, or an exception
    to raise."""

    def __init__(self, outcomes: list[object]) -> None:
        self._outcomes = outcomes
        self.calls = 0

    async def load(self, game_id: GameId):
        self.calls += 1
        outcome = self._outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


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
    without changing the behaviour that matters."""
    clock = FakeClock(T0)
    loader = ScriptedLoader([lobby_state(), *[OSError("db down")] * 4, lobby_state()])
    manager = a_manager(loader, clock=clock, backoff_initial_s=1.0, backoff_max_s=4.0)
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
    assert clock.pending() == ()          # nothing scheduled: it is not coming back
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

    assert loader.calls == 2              # one reload, not two
    entry = manager.entry_for(GAME)
    assert isinstance(entry, Live)
    assert entry.runtime.generation == old.generation + 1
```

Two notes on the helpers these need:

- `a_manager` gains a `subscribers=` override alongside `clock=`, `games=`, `backoff_initial_s=` and `backoff_max_s=`.
- `replace_executor_for_test` is a one-line seam on `GameRuntime` (`self._executor = executor`), used here to make a live runtime fault on its next command. Name it exactly that so it is never mistaken for production API. `consumer_done()` is the accessor added in Task 9.

`ScriptedLoader` moves to `tests/runtime/conftest.py` in Task 13 — write it here and move it there, rather than leaving two copies.

- [ ] **Step 2: Run and watch it fail**

```bash
cd backend && uv run pytest tests/runtime/test_quarantine.py -q --no-cov
```

Expected: `AttributeError: 'GameManager' object has no attribute 'quarantine'`.

- [ ] **Step 3: Implement**

Add to `backend/src/triviador/runtime/manager.py` (and take `backoff_initial_s: float = 1.0`, `backoff_max_s: float = 60.0` in `__init__`, plus a `self._quarantines: dict[GameId, asyncio.Task[None]] = {}`):

```python
    def _on_fault(self, runtime: GameRuntime, exc: BaseException) -> None:
        """Called from inside the faulting consumer task, so it may only
        *schedule*. §5.6: quarantine is "scheduled onto the manager and
        never run by the faulting consumer task" — a task cannot cancel
        and await itself."""
        self.quarantine(runtime, str(exc))

    def quarantine(self, runtime: GameRuntime, reason: str) -> None:
        existing = self._quarantines.get(runtime.game_id)
        if existing is not None and not existing.done():
            # Already tearing this game down. A second report — from the
            # deadline task, or a caller that raced the first — must not
            # start a second teardown that would destroy the replacement
            # generation the first one is about to install.
            return
        self._quarantines[runtime.game_id] = asyncio.create_task(
            self._quarantine(runtime, reason), name=f"quarantine:{runtime.game_id}"
        )

    async def _quarantine(self, runtime: GameRuntime, reason: str) -> None:
        game_id = runtime.game_id
        lock = self._locks.setdefault(game_id, asyncio.Lock())
        async with lock:
            entry = self._entries.get(game_id)
            if isinstance(entry, Live) and entry.runtime is not runtime:
                # A newer generation is already installed; this report is
                # about a runtime nobody can reach any more.
                return

            logger.error("game %s: quarantining generation %d — %s",
                         game_id, runtime.generation, reason)
            self._entries[game_id] = Recovering(attempt=1, next_at=self._clock.now())
            runtime.closed = True
            runtime.drain(RuntimeCode.GAME_RECOVERING, "game is recovering")
            await runtime.aclose()
            # Through the port: the sockets stay owned by the hub, which
            # is the only thing that knows how to close one.
            self._subscribers.close_game_subscribers(game_id, 1011)
            if self._shutting_down:
                # Tear down, but do not start a recovery the process has
                # no intention of finishing.
                del self._entries[game_id]
                return

        await self._recover(game_id)

    async def _recover(self, game_id: GameId) -> None:
        """Bounded exponential backoff, capped and jittered, retried for as
        long as the fault looks transient.

        Attempts are not capped — a database that is down for ten minutes
        must not leave every game in that window permanently `Failed`,
        because nothing about it is permanent. What is capped is the
        *delay*, so a long outage settles into a steady retry rather than
        an ever-growing one.
        """
        attempt = 1
        next_at = self._clock.now()  # bound before the loop: mypy cannot
        # see that the only path reaching the sleep below is the one that
        # assigned it inside the `except`.
        while True:
            lock = self._locks.setdefault(game_id, asyncio.Lock())
            async with lock:
                if self._shutting_down:
                    # The fence. Without this the loop outlives
                    # `shutdown()` and installs a fresh `Live` runtime
                    # into a registry the process has finished with.
                    return
                if not isinstance(self._entries.get(game_id), Recovering):
                    return  # someone unloaded or replaced this game
                try:
                    await self._load(game_id)
                    logger.info("game %s: recovered on attempt %d", game_id, attempt)
                    return
                except GameUnrecoverable:
                    return  # `_load` already recorded Failed
                except Exception as exc:
                    delay = min(
                        self._backoff_max_s, self._backoff_initial_s * (2 ** (attempt - 1))
                    )
                    delay = self._rng.uniform(0.0, delay)
                    attempt += 1
                    next_at = self._clock.now() + timedelta(seconds=delay)
                    self._entries[game_id] = Recovering(attempt=attempt, next_at=next_at)
                    logger.warning(
                        "game %s: recovery attempt %d failed (%s); retrying at %s",
                        game_id, attempt - 1, exc, next_at,
                    )
            # Outside the lock: a `get` during the wait must be able to
            # observe `Recovering` and answer 503 rather than block.
            await self._clock.sleep_until(next_at)
```

`_load` already turns `PermanentReplayFailure` into `Failed` + `GameUnrecoverable`, so the `except GameUnrecoverable: return` clause is what implements "permanent faults go straight to `Failed` without retrying".

Also in this task: add `from datetime import timedelta` to the imports; store `self._backoff_initial_s` and `self._backoff_max_s` from the two new constructor arguments; and add `self._quarantines: dict[GameId, asyncio.Task[None]] = {}` to `__init__`.

- [ ] **Step 4: Verify**

```bash
cd backend && uv run pytest tests/runtime -q --no-cov
uv run ruff check . && uv run ruff format --check . && uv run mypy
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/src/triviador/runtime/manager.py backend/tests/runtime/test_quarantine.py
git commit -m "feat(runtime): quarantine off the faulting task, and recovery that can fail"
```

---

## Task 13: Startup recovery — every active game, loaded at boot

**Files:**
- Modify: `backend/src/triviador/runtime/manager.py`
- Test: `backend/tests/runtime/test_manager.py` (append)

**Interfaces:**
- Consumes: `GameQueriesPort.find_unfinished()` (Task 1 / Plan 3), `GameManager.get` (Task 11).
- Produces: `GameManager.recover_active_games() -> tuple[GameId, ...]`, returning the ids it could not load. Plan 5's startup hook calls it; Task 17 exercises it against real PostgreSQL.

**Why this is not optional (§5.6).** §11.6 forbids evicting active games, because nobody would own their `DeadlineId` — and a restart is exactly an eviction. Without this, every deploy silently pauses every live game until a player happens to reconnect, and the pause is invisible from the server side.

- [ ] **Step 1: Write the failing test**

Append to `backend/tests/runtime/test_manager.py`:

```python
class StubGames:
    def __init__(self, unfinished: tuple[GameId, ...] = ()) -> None:
        self.unfinished = unfinished

    async def find_unfinished(self) -> tuple[GameId, ...]:
        return self.unfinished

    async def find_empty_lobbies(self, *, created_before):
        return ()

    async def find_stale_lobbies(self, *, created_before):
        return ()


async def test_recover_active_games_loads_every_unfinished_game() -> None:
    games = StubGames((GameId("g1"), GameId("g2")))
    manager = a_manager(CountingLoader(), games=games)

    unloadable = await manager.recover_active_games()

    assert unloadable == ()
    assert {r.game_id for r in manager.live_runtimes()} == {GameId("g1"), GameId("g2")}


async def test_recover_active_games_reports_what_it_could_not_load() -> None:
    """One bad game must not stop the other nineteen from coming back. The
    ids it skipped are returned so the caller can log them at error rather
    than discover the gap when a player complains."""
    games = StubGames((GameId("g1"), GameId("g2")))
    manager = a_manager(
        ScriptedLoader([PermanentReplayFailure("bad digest"), lobby_state()]), games=games
    )

    unloadable = await manager.recover_active_games()

    assert unloadable == (GameId("g1"),)
    assert [r.game_id for r in manager.live_runtimes()] == [GameId("g2")]


async def test_recovered_games_rearm_their_deadlines() -> None:
    """§5.6: the rebuilt state carries an absolute `deadline_at`; if it is
    still in the future the deadline task is scheduled for that instant.
    A recovery that came back without a timer is a game that never
    advances again."""
    from tests.runtime.conftest import warmup_state

    state = warmup_state()
    deadline = state.current_deadline()
    assert deadline is not None
    clock = FakeClock(deadline.deadline_at - timedelta(seconds=5))
    manager = a_manager(
        ScriptedLoader([state]), games=StubGames((GameId("g1"),)), clock=clock
    )

    await manager.recover_active_games()
    await clock.settle()

    assert clock.pending() == (deadline.deadline_at,)
```

Move `ScriptedLoader` from `tests/runtime/test_quarantine.py` into `tests/runtime/conftest.py` so both modules use one implementation. Import `timedelta` at the top of the module.

- [ ] **Step 2: Run and watch it fail**

```bash
cd backend && uv run pytest tests/runtime/test_manager.py -q --no-cov
```

Expected: `AttributeError: 'GameManager' object has no attribute 'recover_active_games'`.

- [ ] **Step 3: Implement**

Add to `GameManager`:

```python
    async def recover_active_games(self) -> tuple[GameId, ...]:
        """Load every game the database says is still being played.

        §11.6 forbids evicting active games — nobody would own their
        `DeadlineId` — and a process restart is exactly an eviction.
        Without this, every deploy pauses every live game until a player
        happens to reconnect, and nothing on the server side shows it.

        `find_unfinished` returns `status IN ('expansion', 'battle')`.
        `FinalTiebreak` is inside `battle`; there is no `final` status.
        Lobbies are deliberately excluded: they hold no deadline, so
        loading them at boot would be work with no owner and no timer,
        and the reaper reaches the abandoned ones through the database
        anyway.

        One unloadable game must not stop the rest, so failures are
        collected and returned rather than raised. `_load` has already
        recorded `Failed` for the permanent ones.
        """
        unloadable: list[GameId] = []
        for game_id in await self._games.find_unfinished():
            try:
                await self.get(game_id)
            except Exception as exc:
                logger.error("game %s: could not be recovered at startup — %s", game_id, exc)
                unloadable.append(game_id)
        return tuple(unloadable)
```

- [ ] **Step 4: Verify**

```bash
cd backend && uv run pytest tests/runtime -q --no-cov
uv run ruff check . && uv run ruff format --check . && uv run mypy
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/src/triviador/runtime/manager.py backend/tests/runtime/test_manager.py \
        backend/tests/runtime/conftest.py backend/tests/runtime/test_quarantine.py
git commit -m "feat(runtime): reload every active game at startup, deadlines intact"
```

---

## Task 14: The watchdog

**Files:**
- Create: `backend/src/triviador/runtime/watchdog.py`
- Test: `backend/tests/runtime/test_watchdog.py`

**Interfaces:**
- Consumes: `GameManager.live_runtimes()` (Task 11), `GameRuntime.{state, submit, expiry_enqueued_deadline_id}` (Tasks 9–10), `runtime.origins.SystemOrigin`, `services.ports.Clock`.
- Produces: `runtime.watchdog.Watchdog(manager, clock, interval_s, grace_s)` with `start()` and `async aclose()`. Task 16's shutdown cancels it first.

**§11.5, and the fence that makes it safe.** One task, 5 s tick over resident runtimes: if a current deadline exists, `now > deadline_at + 5 s`, and no expiry has been **enqueued** for that `DeadlineId`, enqueue one. Fencing on `expiry_enqueued_deadline_id` rather than "last expired" is the whole design — otherwise every tick re-enqueues while the first expiry is still waiting in the queue, and a briefly-stalled consumer wakes up to 256 copies of the same command.

- [ ] **Step 1: Write the failing test**

`backend/tests/runtime/test_watchdog.py`:

```python
"""§11.5. The watchdog exists for the case where the deadline task died
without firing — a cancelled task that lost its race, an exception nobody
saw. It must fix that case and no other."""

import random
from datetime import timedelta

import pytest

from tests.runtime.conftest import T0, warmup_state
from tests.runtime.fakes import FakeBroadcaster, FakeClock
from triviador.domain.game.actions import ExpireDeadline
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

    full = stalled_runtime(state, clock, queue_maxsize=1)
    full.submit(QueuedCommand(ExpireDeadline(DeadlineId(1)), "filler", RecordingOrigin()))
    closed = stalled_runtime(state, clock)
    closed.closed = True
    healthy = stalled_runtime(state, clock)

    watchdog = Watchdog(
        manager=manager_holding(full, closed, healthy), clock=clock, grace_s=5.0
    )
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
    watchdog = Watchdog(manager=manager_holding(runtime), clock=clock, grace_s=5.0)

    watchdog.tick()  # ServerBusy: the queue is full
    assert runtime.expiry_enqueued_deadline_id is None  # rolled back

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
    watchdog = Watchdog(
        manager=manager_holding(runtime), clock=clock, interval_s=5.0, grace_s=5.0
    )
    watchdog.start()
    await clock.settle()
    assert queued_commands(runtime) == []  # nothing before the first tick

    await clock.advance_to(clock.now() + timedelta(seconds=5))

    assert [qc.command for qc in queued_commands(runtime)] == [ExpireDeadline(deadline.id)]
    await watchdog.aclose()
```

Four shared test helpers, all in `tests/runtime/conftest.py`:

```python
def stalled_runtime(
    state: GameState, clock: FakeClock, *, queue_maxsize: int = 256
) -> GameRuntime:
    """A runtime that is **not** started: commands accumulate in the queue
    and nothing consumes them. That is exactly the condition the watchdog
    exists to survive — a consumer that is wedged, or a deadline task that
    died without firing."""
    return GameRuntime(
        state=state,
        executor=StubExecutor([]),
        clock=clock,
        broadcaster=FakeBroadcaster(),
        on_fault=lambda rt, exc: None,
        generation=1,
        rng=random.Random(0),
        queue_maxsize=queue_maxsize,
    )


def manager_holding(*runtimes: GameRuntime) -> GameManager:
    """A manager whose registry is populated directly. The watchdog and
    reaper only ever read `live_runtimes()`, so building one through
    `get()` would mean wiring a loader for no gain."""
    manager = a_manager(CountingLoader())
    for runtime in runtimes:
        manager._entries[runtime.game_id] = Live(runtime)
    return manager


def queued_commands(runtime: GameRuntime) -> list[QueuedCommand]:
    """Peek without consuming — `asyncio.Queue` has no public peek, and
    reaching into `_queue` from five test modules is worse than reaching
    into it from one."""
    return list(runtime._queue._queue)


def drain_queue(runtime: GameRuntime) -> list[QueuedCommand]:
    drained = list(runtime._queue._queue)
    runtime._queue._queue.clear()
    return drained
```

`manager_holding` gives every runtime the same `game_id` if they were all built from `lobby_state()`, which would collapse them into one registry entry. In `test_a_full_queue_or_closed_runtime_does_not_kill_the_tick`, give each runtime a distinct game by passing `replace(state, game_id=GameId("g2"))` and so on.

`replace_state_for_test` is a one-line test seam on `GameRuntime` (`self._state = state`), needed because the watchdog tests advance a game without running the consumer. Name it exactly that, so it is never mistaken for production API.

- [ ] **Step 2: Run and watch it fail**

```bash
cd backend && uv run pytest tests/runtime/test_watchdog.py -q --no-cov
```

Expected: `ModuleNotFoundError: No module named 'triviador.runtime.watchdog'`.

- [ ] **Step 3: Implement**

`backend/src/triviador/runtime/watchdog.py`:

```python
"""§11.5. One task, one tick, one job: notice a deadline that should have
fired and did not.

Every rescue it performs is a bug somewhere else — a deadline task that
was cancelled and lost its race, or one that died without firing. It is
cheap insurance against a game silently stopping, and it is written so
that being wrong costs nothing: a spurious expiry is dropped by guard 2.
"""

import asyncio
import logging
from datetime import timedelta
from uuid import uuid4

from triviador.domain.game.actions import ExpireDeadline
from triviador.runtime.errors import RuntimeClosed, ServerBusy
from triviador.runtime.manager import GameManager
from triviador.runtime.origins import SystemOrigin
from triviador.runtime.runtime import QueuedCommand
from triviador.services.ports import Clock

logger = logging.getLogger(__name__)


class Watchdog:
    def __init__(
        self,
        *,
        manager: GameManager,
        clock: Clock,
        interval_s: float = 5.0,
        grace_s: float = 5.0,
    ) -> None:
        self._manager = manager
        self._clock = clock
        self._interval_s = interval_s
        self._grace_s = grace_s
        self._task: asyncio.Task[None] | None = None

    def start(self) -> None:
        self._task = asyncio.create_task(self._run(), name="watchdog")

    async def aclose(self) -> None:
        if self._task is None:
            return
        self._task.cancel()
        try:
            await self._task
        except asyncio.CancelledError:
            pass
        self._task = None

    async def _run(self) -> None:
        while True:
            try:
                await self._clock.sleep_until(
                    self._clock.now() + timedelta(seconds=self._interval_s)
                )
            except asyncio.CancelledError:
                return
            self.tick()

    def tick(self) -> None:
        """Synchronous and total: it must survive any one runtime being
        closed, busy, or otherwise broken, because the other games depend
        on the next tick happening."""
        now = self._clock.now()
        for runtime in self._manager.live_runtimes():
            deadline = runtime.state.current_deadline()
            if deadline is None:
                continue
            if now <= deadline.deadline_at + timedelta(seconds=self._grace_s):
                continue
            if runtime.expiry_enqueued_deadline_id == deadline.id:
                # Fencing on *enqueued*, not on *expired*: the first
                # expiry may still be sitting in the queue behind a slow
                # command, and re-enqueuing every tick would fill the
                # queue with copies of it.
                continue

            previous_fence = runtime.expiry_enqueued_deadline_id
            runtime.expiry_enqueued_deadline_id = deadline.id
            try:
                runtime.submit(
                    QueuedCommand(
                        command=ExpireDeadline(deadline.id),
                        operation_id=f"watchdog-{runtime.game_id}-{deadline.id}-{uuid4()}",
                        origin=SystemOrigin("watchdog"),
                    )
                )
            except (RuntimeClosed, ServerBusy) as exc:
                # Roll the fence back. A fence left set after a failed
                # enqueue makes every subsequent tick skip this deadline —
                # the watchdog would permanently stop watching the one
                # game that most needs it.
                runtime.expiry_enqueued_deadline_id = previous_fence
                logger.warning(
                    "watchdog could not enqueue expiry for game %s: %s", runtime.game_id, exc
                )
            else:
                logger.warning(
                    "watchdog fired expiry for game %s deadline %s — the deadline task did not",
                    runtime.game_id,
                    deadline.id,
                )
```

`tick()` is public so the tests can call it directly without driving the sleep loop; the loop itself gets one test (`_run` ticks on the interval) and the behaviour tests call `tick()`.

- [ ] **Step 4: Verify**

```bash
cd backend && uv run pytest tests/runtime -q --no-cov
uv run ruff check . && uv run ruff format --check . && uv run mypy
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/src/triviador/runtime/watchdog.py backend/tests/runtime/test_watchdog.py \
        backend/tests/runtime/conftest.py
git commit -m "feat(runtime): watchdog that rescues a stalled deadline exactly once"
```

---

## Task 15: The reaper

**Files:**
- Create: `backend/src/triviador/runtime/reaper.py`
- Test: `backend/tests/runtime/test_reaper.py`

**Interfaces:**
- Consumes: `GameQueriesPort.{find_empty_lobbies, find_stale_lobbies}` (Plan 3), `GameManager.{get, live_runtimes, unload}` (Tasks 11 + this task), `GameSubscriberControl.subscriber_count` (Task 1), `domain.game.actions.AbortGame`.
- Produces: `runtime.reaper.Reaper(manager, games, subscribers, clock, ...)` with `start()`, `async tick()`, `async aclose()`; and `GameManager.unload(game_id) -> bool`.

**§11.6's table:**

| Condition | Action |
|---|---|
| `LOBBY`, zero players, older than 5 min | system abort |
| `LOBBY`, older than `LOBBY_MAX_AGE_HOURS` (default 6) | system abort |
| `LOBBY` with no connections | runtime may be unloaded |
| `FINISHED` / `ABORTED` | unload immediately |
| `EXPANSION` / `BATTLE` | **never** unload, regardless of presence |

**The abandoned-lobby sweep queries the database, not resident runtimes.** A resident scan would miss every lobby the no-connections rule had already unloaded, leaving it in the database forever. So the reaper loads the runtime *in order to* abort it — which is also why `AbortGame(actor_id=None)` exists (Plan 2 §3.3): an empty lobby has no actor that could pass guard 3.

- [ ] **Step 1: Write the failing test**

`backend/tests/runtime/test_reaper.py`:

```python
"""§11.6. Two halves that pull in opposite directions: unload what nobody
needs, and never unload what somebody is playing."""

import random
from datetime import timedelta

import pytest

from tests.conftest import lobby_state
from tests.runtime.conftest import T0, warmup_state
from tests.runtime.fakes import FakeClock, FakeSubscribers
from triviador.domain.game.actions import AbortGame
from triviador.domain.game.state import Phase
from triviador.domain.ids import GameId
from triviador.runtime.reaper import Reaper


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


async def test_a_finished_game_is_unloaded() -> None:
    clock = FakeClock(T0)
    manager, runtime = manager_with_resident(finished_state(), clock)
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
```

Three additions to `tests/runtime/conftest.py`:

```python
@dataclass
class StubGames:
    """Extends Task 13's stub with the reaper's two queries, recording the
    cutoff each was called with so the age test can assert on them rather
    than on a mock's call log."""

    unfinished: tuple[GameId, ...] = ()
    empty_lobbies: tuple[GameId, ...] = ()
    stale_lobbies: tuple[GameId, ...] = ()
    empty_cutoffs: list[datetime] = field(default_factory=list)
    stale_cutoffs: list[datetime] = field(default_factory=list)

    async def find_unfinished(self) -> tuple[GameId, ...]:
        return self.unfinished

    async def find_empty_lobbies(self, *, created_before: datetime) -> tuple[GameId, ...]:
        self.empty_cutoffs.append(created_before)
        return self.empty_lobbies

    async def find_stale_lobbies(self, *, created_before: datetime) -> tuple[GameId, ...]:
        self.stale_cutoffs.append(created_before)
        return self.stale_lobbies


def a_reaper(manager, games, clock, *, subscribers=None) -> Reaper:
    return Reaper(
        manager=manager,
        games=games,
        subscribers=subscribers if subscribers is not None else FakeSubscribers(),
        clock=clock,
        empty_lobby_grace_minutes=5,
        lobby_max_age_hours=6,
    )


def manager_with_resident(
    state: GameState, clock: FakeClock, *, start: bool = True, executor=None
) -> tuple[GameManager, GameRuntime]:
    """A manager holding one runtime built directly from `state`, so a
    test can choose the phase without playing a game to reach it."""
    manager = a_manager(CountingLoader(), clock=clock)
    runtime = GameRuntime(
        state=state,
        executor=executor if executor is not None else StubExecutor([]),
        clock=clock,
        broadcaster=FakeBroadcaster(),
        on_fault=lambda rt, exc: None,
        generation=1,
        rng=random.Random(0),
    )
    if start:
        runtime.start()
    manager._entries[runtime.game_id] = Live(runtime)
    return manager, runtime
```

`finished_state()` folds a `GameFinished` onto a started game — build it through the reducer, and if that is more turns than it is worth, reach the terminal phase with `AbortGame(actor_id=None)` instead and assert on `Phase.ABORTED`; §11.6 treats the two identically.

Every runtime built from `lobby_state()` shares `GameId("g1")`, so any test holding two at once must give them distinct ids with `replace(state, game_id=GameId("g2"))`.

- [ ] **Step 2: Run and watch it fail**

```bash
cd backend && uv run pytest tests/runtime/test_reaper.py -q --no-cov
```

Expected: `ModuleNotFoundError: No module named 'triviador.runtime.reaper'`.

- [ ] **Step 3: Add `GameManager.unload`**

```python
    async def unload(self, game_id: GameId) -> bool:
        """Drop a resident runtime that nobody needs. Returns False if it
        was busy and should be retried on a later tick.

        Unloading is not a fault: no origin is resolved with a failure
        code and no subscriber is closed. So a runtime with queued or
        in-flight work is left alone rather than being torn down — the
        alternative is inventing a failure code for "we decided to
        garbage-collect your command", which no client should ever see.

        Three details, each load-bearing:

        1. **`closed` is set before the idle check, not after.** `submit`
           is synchronous and takes no lock, so between "it looks idle"
           and "it is detached" a WebSocket read loop can enqueue a
           command. Closing first makes that submit raise `RuntimeClosed`
           — which the caller handles by re-`get()`ing — instead of
           dropping a command into a queue about to be discarded. If the
           runtime turns out not to be idle, `closed` is rolled back.
        2. **`is_idle()`, not `pending_commands() == 0`.** The consumer
           dequeues before executing, so an empty queue is not an idle
           runtime.
        3. **`stop()`, not `aclose()`.** Even having checked, the only
           safe way to end a consumer is to let it finish: `aclose`
           cancels, and a cancel that lands inside COMMIT manufactures
           the ambiguous-commit case for a runtime we were merely trying
           to garbage-collect.
        """
        lock = self._locks.setdefault(game_id, asyncio.Lock())
        async with lock:
            entry = self._entries.get(game_id)
            if not isinstance(entry, Live):
                return False

            runtime = entry.runtime
            runtime.closed = True
            if not runtime.is_idle():
                runtime.closed = False
                return False

            del self._entries[game_id]
            await runtime.stop()
            return True
```

`pending_commands`, `is_idle`, `_in_flight` and `stop()` all arrive with `GameRuntime` in Task 9, so nothing new is needed on the runtime here.

- [ ] **Step 4: Implement the reaper**

`backend/src/triviador/runtime/reaper.py`:

```python
"""§11.6. Abandoned lobbies get aborted; runtimes nobody needs get unloaded.

The abandoned-lobby sweep queries the *database*, not resident runtimes.
A resident scan would miss every lobby the no-connections rule had already
unloaded — which is most of them, after a few hours — and those rows would
stay in `LOBBY` forever.
"""

import asyncio
import logging
from datetime import timedelta
from uuid import uuid4

from triviador.domain.game.actions import AbortGame
from triviador.domain.game.state import Phase
from triviador.runtime.errors import RuntimeClosed, ServerBusy
from triviador.runtime.manager import GameManager
from triviador.runtime.origins import SystemOrigin
from triviador.runtime.runtime import QueuedCommand
from triviador.services.ports import Clock, GameQueriesPort, GameSubscriberControl

logger = logging.getLogger(__name__)

TERMINAL_PHASES = (Phase.FINISHED, Phase.ABORTED)


class Reaper:
    def __init__(
        self,
        *,
        manager: GameManager,
        games: GameQueriesPort,
        subscribers: GameSubscriberControl,
        clock: Clock,
        interval_s: float = 60.0,
        empty_lobby_grace_minutes: int = 5,
        lobby_max_age_hours: int = 6,
    ) -> None:
        self._manager = manager
        self._games = games
        self._subscribers = subscribers
        self._clock = clock
        self._interval_s = interval_s
        self._empty_lobby_grace = timedelta(minutes=empty_lobby_grace_minutes)
        self._lobby_max_age = timedelta(hours=lobby_max_age_hours)
        self._task: asyncio.Task[None] | None = None

    def start(self) -> None:
        self._task = asyncio.create_task(self._run(), name="reaper")

    async def aclose(self) -> None:
        if self._task is None:
            return
        self._task.cancel()
        try:
            await self._task
        except asyncio.CancelledError:
            pass
        self._task = None

    async def _run(self) -> None:
        while True:
            try:
                await self._clock.sleep_until(
                    self._clock.now() + timedelta(seconds=self._interval_s)
                )
            except asyncio.CancelledError:
                return
            try:
                await self.tick()
            except Exception:
                # One bad tick must not end the reaper for the process's
                # lifetime — the next one may well succeed.
                logger.exception("reaper tick failed")

    async def tick(self) -> None:
        await self._abort_abandoned_lobbies()
        await self._unload_idle_runtimes()

    async def _abort_abandoned_lobbies(self) -> None:
        now = self._clock.now()
        empty = await self._games.find_empty_lobbies(created_before=now - self._empty_lobby_grace)
        stale = await self._games.find_stale_lobbies(created_before=now - self._lobby_max_age)

        for game_id in dict.fromkeys((*empty, *stale)):
            # `dict.fromkeys` rather than a set: a lobby can be both empty
            # and stale, and aborting it twice would enqueue a command the
            # second of which lands on an already-aborted game. Order is
            # preserved so the logs read in query order.
            try:
                runtime = await self._manager.get(game_id)
                runtime.submit(
                    QueuedCommand(
                        # `actor_id=None` — a system abort. An empty lobby
                        # has no participant that could pass guard 3
                        # (Plan 2 §3.3, which exists for exactly this).
                        command=AbortGame(actor_id=None),
                        operation_id=f"reaper-abort-{game_id}-{uuid4()}",
                        origin=SystemOrigin("reaper"),
                    )
                )
            except (RuntimeClosed, ServerBusy) as exc:
                logger.warning("reaper could not abort lobby %s: %s", game_id, exc)
            except Exception:
                # A game that will not load is the manager's problem — it
                # has already been recorded as Failed or Recovering. The
                # sweep continues.
                logger.exception("reaper could not load lobby %s", game_id)

    async def _unload_idle_runtimes(self) -> None:
        for runtime in self._manager.live_runtimes():
            phase = runtime.state.phase
            if phase in TERMINAL_PHASES:
                await self._manager.unload(runtime.game_id)
                continue
            if phase is Phase.LOBBY and self._subscribers.subscriber_count(runtime.game_id) == 0:
                await self._manager.unload(runtime.game_id)
                continue
            # EXPANSION / BATTLE: never unloaded, regardless of presence.
            # Unloading one would orphan its DeadlineId, and the game
            # would stop advancing while looking perfectly healthy
            # (§11.6, and §12.2's presence case).
```

- [ ] **Step 5: Verify**

```bash
cd backend && uv run pytest tests/runtime -q --no-cov
uv run ruff check . && uv run ruff format --check . && uv run mypy
```

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add backend/src/triviador/runtime/reaper.py backend/src/triviador/runtime/manager.py \
        backend/src/triviador/runtime/runtime.py backend/tests/runtime/test_reaper.py
git commit -m "feat(runtime): reaper that aborts abandoned lobbies from the database"
```

---

## Task 16: Graceful shutdown

**Files:**
- Modify: `backend/src/triviador/runtime/manager.py`, `backend/src/triviador/runtime/runtime.py`
- Test: `backend/tests/runtime/test_shutdown.py`

**Interfaces:**
- Consumes: `Watchdog.aclose` (Task 14), `Reaper.aclose` (Task 15), `GameRuntime.{drain, stop, aclose}`.
- Produces: `GameManager.shutdown(watchdog, reaper)` and `GameRuntime.stop()` (enqueue the stop sentinel and await the consumer). Plan 5's lifespan handler calls `shutdown`.

**§5.6's order, and the one thing it forbids:**

```
stop accepting new commands
cancel watchdog and reaper
per runtime:
  drain queued commands, resolving their origins with SERVER_RESTARTING
  allow only the already in-flight transaction to finish
  cancel the deadline task
  close sockets 1001
```

**Cancelling mid-`COMMIT` would manufacture the ambiguous-commit case on every deploy** — the one failure mode never worth generating deliberately. So the consumer is never cancelled: it is asked to stop, and it finishes whatever it is doing first.

- [ ] **Step 1: Write the failing test**

`backend/tests/runtime/test_shutdown.py`:

```python
"""§5.6's shutdown. The assertion that matters is the negative one: no
transaction is cancelled part-way."""

import asyncio
import random

import pytest

from tests.conftest import lobby_state
from tests.runtime.conftest import T0
from tests.runtime.fakes import FakeClock, FakeSubscribers, RecordingOrigin
from triviador.domain.game.actions import JoinGame
from triviador.domain.ids import GameId, PlayerId
from triviador.runtime.errors import ServerRestarting
from triviador.runtime.runtime import QueuedCommand
from triviador.services.ports import RuntimeCode


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
    assert not shutdown.done()          # waiting on the transaction, not cancelling it

    executor.release.set()
    await shutdown

    assert origin.outcome[0] == "ok"    # committed, not SERVER_RESTARTING
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
```

Two helpers this module adds:

```python
@dataclass
class TracingCloser:
    """Stands in for the watchdog/reaper, recording when it was closed so
    the ordering against the runtime drain is observable."""

    trace: list[str]
    label: str

    async def aclose(self) -> None:
        self.trace.append(self.label)
```

`on_drain_for_test` is an optional callback on `GameRuntime.drain` (`if self.on_drain_for_test is not None: self.on_drain_for_test()`), defaulting to `None`. It exists only to make ordering observable; name it exactly that so it is never mistaken for production API. `manager_with_resident` gains a `subscribers=` parameter that it forwards to `a_manager`.

- [ ] **Step 2: Run and watch it fail**

```bash
cd backend && uv run pytest tests/runtime/test_shutdown.py -q --no-cov
```

Expected: `AttributeError: 'GameManager' object has no attribute 'shutdown'`.

- [ ] **Step 3: Implement `GameManager.shutdown`**

`GameRuntime.stop()` already exists (Task 9). Shutdown calls `drain` first so the queue has room for the sentinel even if it was full, then `stop()`.

```python
    async def shutdown(self, *closers: SupportsAclose) -> None:
        """§5.6, in an order chosen so that nothing can be resurrected
        behind it.

        The subtle failure this guards against: `_recover` is an
        unbounded retry loop living on a task the manager spawned, and it
        installs a fresh `Live` runtime when it finally succeeds. Every
        `await` below is a chance for it to do exactly that. Draining the
        `Live` entries and returning would therefore leave a process that
        has "shut down" still holding a running consumer, an open
        deadline task, and a database connection — and the loop would
        never have noticed, because it only ever inspected `Live`.

        So: fence first, then mark, then stop everything the manager
        owns, and only then tear the runtimes down.
        """
        if self._shutting_down:
            return

        # 1. Fence. `get` now raises ServerRestarting, `_load` refuses,
        #    and every `_recover` loop exits at its next check — before
        #    any `await` below can give one a turn.
        self._shutting_down = True

        # 2. Mark every resident runtime closed *before* awaiting
        #    anything. From here no submit succeeds anywhere, so a
        #    watchdog or reaper tick already in flight cannot enqueue
        #    into a queue that is about to be drained.
        for entry in self._entries.values():
            if isinstance(entry, Live):
                entry.runtime.closed = True

        # 3. Stop the background tasks: the caller's watchdog and reaper,
        #    then the manager's own quarantine/recovery tasks. These are
        #    awaited, not just cancelled — a quarantine task cancelled
        #    mid-teardown could leave a runtime detached but still
        #    consuming.
        for closer in closers:
            await closer.aclose()
        await self._cancel_lifecycle_tasks()

        # 4. Now the runtimes, which are the only things left running.
        for game_id, entry in list(self._entries.items()):
            if isinstance(entry, Live):
                runtime = entry.runtime
                runtime.drain(RuntimeCode.SERVER_RESTARTING, "server is restarting")
                await runtime.stop()
                self._subscribers.close_game_subscribers(game_id, 1001)
            del self._entries[game_id]

    async def _cancel_lifecycle_tasks(self) -> None:
        tasks = [t for t in self._quarantines.values() if not t.done()]
        for task in tasks:
            task.cancel()
        for task in tasks:
            try:
                await task
            except asyncio.CancelledError:
                pass
            except Exception:
                logger.exception("quarantine task failed during shutdown")
        self._quarantines.clear()
```

One supporting change is still needed here; the other three already landed in Tasks 11 and 12, because a flag added later to a method written earlier is precisely how this race got in:

- **New in this task:** `get` raises `ServerRestarting` when `self._shutting_down` is set. That is "stop accepting new commands", enforced at the one place a caller can reach a runtime.
- Already present: `self._shutting_down = False` in `__init__` and the `_load` guard (Task 11); the `_recover` and `_quarantine` checks (Task 12). Verify all four are in place before writing `shutdown` — the fence works only if every path that can install a `Live` entry consults it.

Declare `SupportsAclose` as a local `Protocol` with `async def aclose(self) -> None: ...` rather than importing the watchdog and reaper into the manager, which would make the dependency circular.

- [ ] **Step 5: Verify**

```bash
cd backend && uv run pytest tests/runtime -q --no-cov
uv run pytest -q
uv run ruff check . && uv run ruff format --check . && uv run mypy
```

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add backend/src/triviador/runtime backend/tests/runtime/test_shutdown.py
git commit -m "feat(runtime): graceful shutdown that never cancels a transaction mid-commit"
```

---

## Task 17: The runtime against real PostgreSQL

**Files:**
- Create: `backend/tests/runtime/integration/__init__.py`, `backend/tests/runtime/integration/conftest.py`, `backend/tests/runtime/integration/test_play_through.py`, `backend/tests/runtime/integration/test_ambiguous_commit.py`, `backend/tests/runtime/integration/test_recovery.py`
- Modify: `backend/pyproject.toml` if a new marker or testpath is needed (it should not be — `integration` already exists)

**Interfaces:**
- Consumes: everything Tasks 1–16 produced, plus `tests/db/conftest.py`'s engine, migrated schema, and truncation fixtures.
- Produces: nothing importable. This task exists to prove the fakes did not lie.

**Why the fast-lane suite is not enough.** Every test up to here runs against a fake unit of work. `FOR SHARE` semantics, `UNIQUE(game_id, seq)`, `TIMESTAMPTZ` round-tripping and real transaction rollback are precisely the things a fake cannot get wrong — and precisely the things that break in production. Spec 1 §12.2 asks for this layer by name.

**How to read this task.** Unlike Tasks 1–16, the tests below are given as signatures plus a docstring that states exactly what to do and what to assert, not as finished code. That is deliberate: each one depends on fixture details that live in `tests/db/conftest.py` and `tests/db/test_question_bank.py`, and writing the bodies against a guess at those fixtures would produce code that has to be rewritten on first run. **Read those two files first**, then turn each docstring into executable code — the docstring is the specification, and a test whose assertions do not match its docstring is a failed task, not a judgement call.

- [ ] **Step 1: Write the fixtures**

`backend/tests/runtime/integration/conftest.py`. **Read `tests/db/conftest.py` first** — three of its properties dictate this file's shape:

- `engine` is **session-scoped**, and asyncpg binds connections to the loop they were created on. So every module here needs `pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="session")]`, exactly as `tests/db` modules do.
- Its fixtures are **directory-scoped** to `tests/db`, so they are invisible here. Re-export them by importing the names.
- Its `pytest_collection_modifyitems` guard filters to items under `tests/db`, so it does **not** cover this directory. Re-declare the guard, or a module that forgets its marks fails at runtime with an opaque "attached to a different loop" error instead of at collection.

```python
"""Wires the real adapters onto `tests/db`'s database fixtures.

Everything under here is the runtime running against PostgreSQL: a real
`UnitOfWork`, a real `GameRepository`, a real `QuestionBank`, a real map
on disk with a real digest. Only the clock, the broadcaster and the
subscriber control stay fake — the first because §12.2 forbids waiting on
wall-clock time, the other two because they are Plan 5's.
"""

import random
from datetime import UTC, datetime
from pathlib import Path

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

# Re-exported so this directory can use them: conftest fixtures do not
# reach sideways across sibling directories.
from tests.db.conftest import (  # noqa: F401
    _lacks_session_loop_scope,
    clean_db,
    engine,
    migrated_schema,
    sessions,
)
from tests.runtime.fakes import FakeBroadcaster, FakeClock, FakeSubscribers
from triviador.db.repositories.games import GameRepository
from triviador.db.unit_of_work import UnitOfWork
from triviador.domain.game.rules import DEFAULT_RULES
from triviador.domain.ids import GameId, MapId, PlayerId
from triviador.maps.registry import MapRegistry
from triviador.runtime.loader import GameLoader
from triviador.runtime.manager import GameManager
from triviador.runtime.materialiser import Materialiser

HERE = Path(__file__).parent
T0 = datetime(2026, 8, 17, 12, 0, tzinfo=UTC)


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    """The same guard `tests/db/conftest.py` applies to its own directory.
    That hook filters to items under `tests/db`, so this directory would
    otherwise be unguarded — and an unmarked module here fails at runtime
    with an opaque cross-loop error rather than at collection."""
    ours = [item for item in items if item.path.is_relative_to(HERE)]
    unmarked = sorted({i.nodeid.split("::")[0] for i in ours if "integration" not in i.keywords})
    missing_loop = sorted({i.nodeid.split("::")[0] for i in ours if _lacks_session_loop_scope(i)})
    if unmarked or missing_loop:
        raise pytest.UsageError(
            "tests/runtime/integration modules must declare `pytestmark = "
            '[pytest.mark.integration, pytest.mark.asyncio(loop_scope="session")]`; '
            f"missing marker in: {unmarked}; missing loop scope in: {missing_loop}"
        )


@pytest.fixture
def map_root(tmp_path: Path) -> Path:
    """Write `tests/conftest.grid_map()` out as a real `map.json`, so
    `map_sha256` is the digest of a real file a test can rewrite under a
    live game."""
    write_grid_map(tmp_path / "grid")
    return tmp_path


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock(T0)


@pytest.fixture
def broadcaster() -> FakeBroadcaster:
    return FakeBroadcaster()


@pytest.fixture
def subscribers() -> FakeSubscribers:
    return FakeSubscribers()


@pytest_asyncio.fixture(loop_scope="session")
async def manager(
    clean_db: None,
    sessions: async_sessionmaker[AsyncSession],
    clock: FakeClock,
    map_root: Path,
    broadcaster: FakeBroadcaster,
    subscribers: FakeSubscribers,
) -> GameManager:
    uow = UnitOfWork(sessions)
    return GameManager(
        loader=GameLoader(uow=uow, maps=MapRegistry(root=map_root)),
        uow=uow,
        materialiser=Materialiser(clock=clock, rng=random.Random(1234)),
        clock=clock,
        broadcaster=broadcaster,
        subscribers=subscribers,
        games=GameRepository(sessions),
        rng=random.Random(1234),
    )


@pytest_asyncio.fixture(loop_scope="session")
async def lobby(
    manager: GameManager, sessions: async_sessionmaker[AsyncSession], map_root: Path
) -> GameId:
    """A `games` row plus its genesis event, written through the real
    `GameRepository.create` — the same path Plan 5's create endpoint will
    take.

    Seeds the three `users` rows the foreign keys require, and enough
    active questions to cover `required_question_budget(DEFAULT_RULES)`:
    **17 numeric and 12 multiple-choice**. Seed exactly that, not a round
    number — a suite that seeds 50 of each would never notice the budget
    changing, and Spec 1B's open item 3 is about precisely this floor.
    """
    for pid in ("p1", "p2", "p3"):
        await seed_user(sessions, pid)
    await seed_question_bank(sessions, numeric=17, multiple_choice=12)

    game_id = GameId("g1")
    digest = MapRegistry(root=map_root).load_with_digest(MapId("grid")).sha256
    await GameRepository(sessions).create(
        game_id=game_id,
        map_id=MapId("grid"),
        rules=DEFAULT_RULES,
        host_id=PlayerId("p1"),
        map_sha256=digest,
        preset_id=None,
        operation_id="genesis-1",
    )
    return game_id
```

`seed_user`, `seed_question_bank` and `write_grid_map` are module-level helpers in this same file:

- Build the seeding by **lifting** `_seed_user`, `_seed_category`, `_seed_numeric_question` and `_seed_mc_question` out of `tests/db/test_question_bank.py` into `tests/db/conftest.py`, then importing them here. Do not write a second set — that file already gets every column right, including `prompt_hash` and the `question_numeric` child row. `seed_question_bank` is a loop over the last two, plus one category.
- `write_grid_map` serializes `tests/conftest.grid_map()` into the JSON shape `MapRegistry.load_with_digest` parses. Read `maps/registry.py` for the exact keys rather than guessing at them.

Also add these query and seam helpers here, used across all three modules below:

```python
async def drain_runtime(runtime: GameRuntime, *, max_turns: int = 200) -> None:
    """Settle the fake clock until the runtime goes idle.

    Bounded, and it raises rather than looping: a wedged consumer must
    fail the test that provoked it, not hang the suite until CI times out
    with no indication of which test was responsible.
    """
    clock = runtime.clock
    assert isinstance(clock, FakeClock)
    for _ in range(max_turns):
        await clock.settle()
        if runtime.is_idle():
            return
    raise AssertionError(f"game {runtime.game_id} never went idle")


async def submit_and_settle(
    runtime: GameRuntime, command: Command, operation_id: str
) -> RecordingOrigin:
    origin = RecordingOrigin()
    runtime.submit(QueuedCommand(command, operation_id, origin))
    await drain_runtime(runtime)
    return origin


def fresh_manager(old: GameManager) -> GameManager:
    """A second `GameManager` over the same sessionmaker, clock and map
    root — the "process restarted" simulation. Everything durable is
    shared; everything in memory is new."""


async def game_status(sessions, game_id) -> str: ...
async def last_seq(sessions, game_id) -> int: ...
async def event_row_count(sessions, game_id) -> int: ...
async def event_seqs(sessions, game_id) -> list[int]: ...
async def player_seats(sessions, game_id) -> dict[str, int]: ...
async def deactivate_all_questions(sessions) -> None: ...
async def rewrite_every_question_prompt(sessions, prefix: str) -> None: ...
def rewrite_map_adding_a_region(map_dir: Path) -> None: ...
```

Each query helper is a two-to-four line `SELECT` over the models in `db/models/games.py` and `db/models/content.py`, following the shape of `_get_game` / `_event_rows` in `tests/db/test_event_store.py`. Write them out; the ellipses above are signatures, not bodies to leave empty.

- [ ] **Step 2: Write `test_play_through.py`**

```python
"""Lobby to a terminal phase, through the real thing. If this passes, the
fakes were telling the truth."""

import pytest

from triviador.domain.game.actions import AbortGame, JoinGame, RejectCode, StartGame
from triviador.domain.game.state import Phase
from triviador.domain.ids import PlayerId
from triviador.runtime.manager import Live

pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="session")]


async def join_all(runtime) -> None:
    for pid in ("p1", "p2", "p3"):
        origin = await submit_and_settle(runtime, JoinGame(PlayerId(pid), pid.upper()), f"join-{pid}")
        assert origin.outcome[0] == "ok"


async def test_three_joins_and_a_start_reach_expansion(manager, lobby, sessions) -> None:
    """The path every game takes. Asserts against the *database*, not just
    memory: `last_seq` must equal the row count, or the read model and the
    log have diverged."""
    runtime = await manager.get(lobby)
    await join_all(runtime)

    origin = await submit_and_settle(runtime, StartGame(PlayerId("p1")), "start-1")

    assert origin.outcome[0] == "ok"
    assert runtime.state.phase is Phase.EXPANSION
    assert await game_status(sessions, lobby) == "expansion"
    assert await event_row_count(sessions, lobby) == runtime.state.seq
    assert await last_seq(sessions, lobby) == runtime.state.seq


async def test_the_read_model_matches_the_folded_state(manager, lobby, sessions) -> None:
    """§4.2's projection, checked end to end: reload with a fresh loader
    and assert the rebuilt state agrees with the `games` / `game_players`
    rows on phase, seq, and seat assignment."""
    runtime = await manager.get(lobby)
    await join_all(runtime)
    await submit_and_settle(runtime, StartGame(PlayerId("p1")), "start-1")

    rebuilt = await fresh_manager(manager)._loader.load(lobby)

    assert rebuilt.phase is runtime.state.phase
    assert rebuilt.seq == runtime.state.seq
    assert await player_seats(sessions, lobby) == {
        pid: player.seat for pid, player in rebuilt.players.items()
    }


async def test_bases_are_mutually_non_adjacent_in_the_committed_log(manager, lobby) -> None:
    """Spec 1 §3.4, asserted where it finally becomes durable. The
    materialiser chose these regions and nothing downstream checks them —
    `_decide_start` validates distinctness and membership only."""
    runtime = await manager.get(lobby)
    await join_all(runtime)
    await submit_and_settle(runtime, StartGame(PlayerId("p1")), "start-1")

    bases = {player.base_region for player in runtime.state.players.values()}
    assert len(bases) == 3
    assert None not in bases
    for region in bases:
        assert runtime.state.map.neighbours(region).isdisjoint(bases)


async def test_a_start_with_a_drained_bank_is_rejected_and_the_game_stays_in_lobby(
    manager, lobby, sessions
) -> None:
    """§10.6's authoritative checkpoint — genuinely authoritative, because
    the `FOR SHARE` locks are still held when the events would be
    inserted. The rejection must leave no trace at all."""
    runtime = await manager.get(lobby)
    await join_all(runtime)
    seq_before = runtime.state.seq
    await deactivate_all_questions(sessions)

    origin = await submit_and_settle(runtime, StartGame(PlayerId("p1")), "start-1")

    assert origin.outcome == ("rejected", RejectCode.QUESTION_POOL_INSUFFICIENT)
    assert runtime.state.phase is Phase.LOBBY
    assert runtime.state.seq == seq_before
    assert await game_status(sessions, lobby) == "lobby"
    assert await event_row_count(sessions, lobby) == seq_before
    assert isinstance(manager.entry_for(lobby), Live)  # a rejection is not a fault


async def test_concurrent_commands_produce_a_contiguous_seq(manager, lobby, sessions) -> None:
    """§12.2's serialization case: N commands from M origins at once →
    seq contiguous, `UNIQUE(game_id, seq)` intact, every origin resolved
    exactly once. The consumer serializes them; this proves the database
    agrees, and that no origin was dropped on the way."""
    runtime = await manager.get(lobby)
    origins = [RecordingOrigin() for _ in range(3)]
    for pid, origin in zip(("p1", "p2", "p3"), origins, strict=True):
        runtime.submit(QueuedCommand(JoinGame(PlayerId(pid), pid.upper()), f"join-{pid}", origin))
    await drain_runtime(runtime)

    assert all(len(o.resolutions) == 1 for o in origins)
    seqs = await event_seqs(sessions, lobby)
    assert seqs == list(range(1, len(seqs) + 1))
    assert await last_seq(sessions, lobby) == seqs[-1]


async def test_an_abort_reaches_a_terminal_phase_and_the_read_model(
    manager, lobby, sessions
) -> None:
    """The short path to a terminal status. A full play-through to
    FINISHED would be worth having, but §11.6 treats ABORTED identically
    and this pins the projection either way."""
    runtime = await manager.get(lobby)
    await submit_and_settle(runtime, JoinGame(PlayerId("p1"), "P1"), "join-p1")

    origin = await submit_and_settle(runtime, AbortGame(actor_id=None), "abort-1")

    assert origin.outcome[0] == "ok"
    assert runtime.state.phase is Phase.ABORTED
    assert await game_status(sessions, lobby) == "aborted"
```

- [ ] **Step 3: Write `test_ambiguous_commit.py`**

```python
"""§12.2: drop the connection during COMMIT → reconciliation by
operation_id, no duplicate batch, no lost batch.

The whole point of these three is that the executor *cannot* tell the
cases apart from the exception it caught. It has to ask the database.
"""

from contextlib import asynccontextmanager

import pytest

from triviador.db.unit_of_work import TransactionContext, UnitOfWork
from triviador.domain.game.actions import JoinGame
from triviador.domain.game.events import PlayerJoined
from triviador.domain.ids import PlayerId
from triviador.runtime.manager import Live, Recovering
from triviador.services.ports import RuntimeCode

pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="session")]


class BreakingUnitOfWork:
    """Wraps a real `UnitOfWork` and breaks the COMMIT of its first
    transaction.

    `mode="landed"` commits and *then* raises: the write is durable and
    the caller was told it failed. `mode="lost"` rolls back and then
    raises: identical signal, opposite truth. Later transactions pass
    straight through, so the retry and the reconciliation both run
    against a healthy connection — which is what happens in production,
    where the pool hands out a new one.
    """

    def __init__(self, inner: UnitOfWork, mode: str) -> None:
        self._inner = inner
        self._mode = mode
        self._broken = False

    @asynccontextmanager
    async def begin(self):
        if self._broken:
            async with self._inner.begin() as tx:
                yield tx
            return

        self._broken = True
        async with self._inner._sessionmaker() as session:
            await session.begin()
            yield TransactionContext(session)
            if self._mode == "landed":
                await session.commit()
            else:
                await session.rollback()
            raise OSError("connection reset during COMMIT")


async def test_a_commit_that_lands_but_reports_failure_is_reconciled(
    manager, lobby, sessions
) -> None:
    """Exactly one batch in `game_events` — never two — the origin
    resolved `ok`, `last_seq` advanced once, and the runtime still Live."""
    runtime = await manager.get(lobby)
    runtime.replace_executor_for_test(
        executor_over(BreakingUnitOfWork(UnitOfWork(sessions), mode="landed"), manager)
    )

    origin = await submit_and_settle(runtime, JoinGame(PlayerId("p1"), "P1"), "join-p1")

    assert origin.outcome[0] == "ok"
    assert await event_row_count(sessions, lobby) == 2  # genesis + one join
    assert await last_seq(sessions, lobby) == 2
    assert isinstance(manager.entry_for(lobby), Live)


async def test_a_commit_that_does_not_land_is_retried(manager, lobby, sessions) -> None:
    """Reconciliation answers ABSENT, the executor re-runs the whole
    attempt, and exactly one batch ends up committed — by the retry, not
    by the original."""
    runtime = await manager.get(lobby)
    runtime.replace_executor_for_test(
        executor_over(BreakingUnitOfWork(UnitOfWork(sessions), mode="lost"), manager)
    )

    origin = await submit_and_settle(runtime, JoinGame(PlayerId("p1"), "P1"), "join-p1")

    assert origin.outcome[0] == "ok"
    assert await event_row_count(sessions, lobby) == 2
    assert await last_seq(sessions, lobby) == 2


async def test_a_foreign_batch_under_the_same_operation_id_quarantines(
    manager, lobby, sessions
) -> None:
    """MISMATCH is never "close enough".

    Pre-write a *different* batch under the `operation_id` the command
    will use, then force the ambiguous path. Reconciliation finds rows
    for that operation whose ordered types are not the ones this attempt
    decided, and quarantines rather than adopting them.

    Note the pre-write also advances `last_seq`, so on some interleavings
    the attempt's own `append` raises `ConcurrentModification` first.
    Both routes quarantine, which is what is asserted — this test pins
    the outcome, and `tests/db/test_reconciliation.py` pins the
    `MISMATCH` verdict itself in isolation.
    """
    runtime = await manager.get(lobby)
    async with UnitOfWork(sessions).begin() as tx:
        await tx.append(
            lobby,
            expected_last_seq=1,
            events=[PlayerJoined(PlayerId("p9"), "P9", seat=0)],
            operation_id="join-p1",
        )
    runtime.replace_executor_for_test(
        executor_over(BreakingUnitOfWork(UnitOfWork(sessions), mode="landed"), manager)
    )

    origin = await submit_and_settle(runtime, JoinGame(PlayerId("p1"), "P1"), "join-p1")

    assert origin.outcome == ("failed", RuntimeCode.GAME_RECOVERING)
    entry = manager.entry_for(lobby)
    assert isinstance(entry, Live | Recovering)
    if isinstance(entry, Live):
        assert entry.runtime.generation > runtime.generation
```

`replace_executor_for_test` is the Task 12 seam; `executor_over(uow, manager)` builds a `CommandExecutor` over a given unit of work with the manager's materialiser, clock and rng — add it to the integration `conftest.py`.

- [ ] **Step 4: Write `test_recovery.py`**

```python
"""§5.6 and §12.2's recovery cases, against a real log."""

from datetime import timedelta

import pytest

from triviador.domain.game.actions import JoinGame, StartGame
from triviador.domain.ids import PlayerId
from triviador.runtime.errors import GameUnrecoverable
from triviador.runtime.manager import Failed

pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="session")]


async def started_game(manager, lobby):
    runtime = await manager.get(lobby)
    for pid in ("p1", "p2", "p3"):
        await submit_and_settle(runtime, JoinGame(PlayerId(pid), pid.upper()), f"join-{pid}")
    await submit_and_settle(runtime, StartGame(PlayerId("p1")), "start-1")
    return runtime


async def test_a_restart_rebuilds_state_identical_to_the_live_one(manager, lobby) -> None:
    """`GameState` is a frozen dataclass all the way down, so `==` is the
    whole assertion.

    If it fails on a field you did not expect — `next_deadline_id` is the
    likely one — that is a finding about recovery, not a reason to loosen
    this to a field-by-field subset.
    """
    runtime = await started_game(manager, lobby)
    snapshot = runtime.state
    await manager.shutdown()

    revived = await fresh_manager(manager).get(lobby)

    assert revived.state == snapshot


async def test_a_deadline_still_in_the_future_is_scheduled_at_its_original_instant(
    manager, lobby, clock
) -> None:
    """§12.2: deadline +20 s, kill the runtime, restart before it → the
    timer fires at the original absolute time, not the full window again
    from the restart."""
    runtime = await started_game(manager, lobby)
    deadline = runtime.state.current_deadline()
    assert deadline is not None
    await manager.shutdown()

    await clock.advance_to(deadline.deadline_at - timedelta(seconds=2))
    revived_manager = fresh_manager(manager)
    await revived_manager.recover_active_games()
    await clock.settle()

    assert clock.pending() == (deadline.deadline_at,)


async def test_a_deadline_already_passed_is_expired_immediately(manager, lobby, clock) -> None:
    """Restart after the window closed → `ExpireDeadline` is enqueued at
    once. Recovery must never extend a window a player has already
    spent."""
    runtime = await started_game(manager, lobby)
    deadline = runtime.state.current_deadline()
    assert deadline is not None
    await manager.shutdown()

    await clock.advance_to(deadline.deadline_at + timedelta(seconds=5))
    revived_manager = fresh_manager(manager)
    await revived_manager.recover_active_games()
    revived = await revived_manager.get(lobby)
    await drain_runtime(revived)

    current = revived.state.current_deadline()
    assert current is not None
    assert current.id != deadline.id  # the expiry fired and the game advanced


async def test_the_question_pool_survives_a_rewrite_of_the_questions_table(
    manager, lobby, sessions
) -> None:
    """§12.2's pool immutability. The pool lives in the committed
    `QuestionPoolDrawn` event, not in the table — rewriting every row must
    not change a single presented question."""
    runtime = await started_game(manager, lobby)
    pool_before = runtime.state.pool
    await manager.shutdown()

    await rewrite_every_question_prompt(sessions, "TAMPERED")

    revived = await fresh_manager(manager).get(lobby)

    assert revived.state.pool == pool_before
    assert all("TAMPERED" not in q.prompt for q in revived.state.pool.numeric)
    assert all("TAMPERED" not in q.prompt for q in revived.state.pool.multiple_choice)


async def test_a_map_digest_mismatch_makes_the_game_unrecoverable(
    manager, lobby, map_root
) -> None:
    """Rewrite `map.json` under a live game. Every region id in the log may
    now name a different region, so replay must refuse outright — not fold
    against different adjacency and carry on looking healthy."""
    runtime = await manager.get(lobby)
    await submit_and_settle(runtime, JoinGame(PlayerId("p1"), "P1"), "join-p1")
    await manager.shutdown()

    rewrite_map_adding_a_region(map_root / "grid")

    revived_manager = fresh_manager(manager)
    with pytest.raises(GameUnrecoverable):
        await revived_manager.get(lobby)

    assert isinstance(revived_manager.entry_for(lobby), Failed)
    assert [game_id for game_id, _ in revived_manager.degraded()] == [lobby]


async def test_startup_recovery_skips_lobbies_and_loads_active_games(manager, lobby) -> None:
    """`find_unfinished` is `status IN ('expansion', 'battle')`. A lobby
    holds no deadline, so loading it at boot would be work with no owner
    and no timer — and the reaper reaches the abandoned ones through the
    database anyway."""
    await started_game(manager, lobby)
    await manager.shutdown()

    revived_manager = fresh_manager(manager)
    unloadable = await revived_manager.recover_active_games()

    assert unloadable == ()
    assert [rt.game_id for rt in revived_manager.live_runtimes()] == [lobby]
```

- [ ] **Step 5: Run the full integration lane**

```bash
cd backend && docker compose -f docker-compose.test.yml up -d
uv run pytest tests/runtime/integration -q --no-cov
uv run pytest tests/db tests/runtime -q --no-cov
uv run pytest -q
uv run ruff check . && uv run ruff format --check . && uv run mypy
```

Then confirm the integration lane fails loudly rather than skipping with the database down:

```bash
docker compose -f docker-compose.test.yml down
uv run pytest tests/runtime/integration -q --no-cov   # must ERROR, never "skipped"
uv run pytest -m "not integration" -q                 # must pass with the database down
```

- [ ] **Step 6: Commit**

```bash
git add backend/tests/runtime/integration
git commit -m "test(runtime): the runtime against real PostgreSQL, end to end"
```

---

## Done criteria

- [ ] `uv run pytest -m "not integration" -q` passes **with the test database stopped**, including all of Plans 1–3's suites unchanged.
- [ ] `uv run pytest tests/db tests/runtime/integration -q --no-cov` passes against a live PostgreSQL 17, and **fails loudly** — never skips — when the database is down.
- [ ] `tests/test_layering.py` proves `domain/` imports no persistence code, `services/` imports no adapter, and `runtime/` imports no `db` or `api` module — in both the absolute and the relative import form.
- [ ] `services/ports.py` mentions no SQLAlchemy type, no `asyncpg`, and no `triviador.db` symbol, and the Plan 3 adapters satisfy every port under `mypy --strict`.
- [ ] A `StartGame` retry after `40001` has been **watched to re-draw the pool** — not merely to re-append — and to reach that retry **without** a reconciliation round trip, even though the failure arrived at COMMIT.
- [ ] Reconciliation has been watched to reject a batch whose `seq` range does not match exactly, and to accept the one that does.
- [ ] A failed expiry enqueue has been watched to leave `expiry_enqueued_deadline_id` clear, so the next watchdog tick still fires.
- [ ] The reaper has been watched to leave a runtime alone while it is executing a command with an empty queue, and to restore `closed` when it finds one busy.
- [ ] A game in `Recovering` has been watched **not** to install a runtime after `shutdown()` returned, and every quarantine task is `done()` at that point.
- [ ] The permanent/transient split has been watched in both directions on each of its three inputs: a decode error and an `InvalidMapError` and a non-folding log are permanent; a database error and an `OSError` from the map provider are not.
- [ ] Quarantine teardown has been watched to run off the faulting task — the consumer task actually finishes.
- [ ] Nothing queued against generation *N* has been observed reaching generation *N+1*.
- [ ] The watchdog has been watched **not** to double-enqueue while an expiry is still queued, and to fire again once the `DeadlineId` changes.
- [ ] The reaper has been watched to abort a lobby that exists only in the database, and to leave an `EXPANSION` game resident with zero connections.
- [ ] Shutdown has been watched to drain with `SERVER_RESTARTING` while letting an in-flight transaction finish.
- [ ] A REST request cancelled after its command was enqueued still commits, and the runtime stays healthy.
- [ ] A broadcaster that raises after commit has been observed leaving the runtime `Live`.
- [ ] Quarantine with persistence down enters `Recovering` and retries with a growing, capped backoff; a permanent replay failure enters `Failed` without retrying.
- [ ] Recovery honours an absolute deadline in both directions: future → scheduled at the original instant, past → `ExpireDeadline` enqueued immediately.
- [ ] A `map_sha256` mismatch has been watched to produce `Failed`, and `degraded()` names the game.
- [ ] Every origin in every test resolved exactly once. `RecordingOrigin.outcome` asserts this; no test bypasses it.
- [ ] No test anywhere in `tests/runtime` calls `asyncio.sleep` with a non-zero argument.
- [ ] `reducer.py` and `db/codec/` are still at 100 % branch coverage.
- [ ] `ruff check`, `ruff format --check`, and `mypy --strict` are clean.

---

## What this plan does not do

- **No HTTP, no WebSocket, no projection.** The REST surface, the `/ws` hub, per-viewer projection, the error envelope, origin checking, and contract export are Plan 5. This plan builds what they call, and `Broadcaster` / `GameSubscriberControl` are the two seams they plug into.
- **No broadcast-side failure handling.** §5.5's second table — outbound queue overflow closes *that* subscriber with `4408`, a projection or serialization failure closes it with `1011` — is behaviour *inside* the broadcaster, and so is Spec 1 §12.2's backpressure scenario (a client that never reads must not stall the loop). Both belong to Plan 5's hub. What this plan owns is the half that protects the game: a `publish` that raises is logged and never quarantines, asserted in Task 9.
- **No game creation.** `GameRepository.create` (Plan 3) writes the `games` row and the genesis event; the endpoint that calls it, and §6.2's genesis flow, are Plan 5. The runtime only ever *loads* games that already exist.
- **No auth.** Commands carry a `PlayerId` and the runtime trusts it. Deriving that id from a session principal, and refusing a frame that names someone else, is Plan 5's job and is asserted there — §11 is explicit that actor identity is asserted once, in the layer that enforces it.
- **No `MediaStore` or `ImportStagingStore` ports.** Nothing in Plans 4 or 5 calls them; they arrive with Plan 7's media pipeline, which is what will know their real shape.
- **No operator endpoint for clearing `Failed`.** §5.6 says `Failed` is "cleared only by operator action"; `degraded()` exposes the state and Plan 5 adds the endpoint that acts on it.
- **No snapshots.** A game is a few hundred events and `fold` on restart is instant (Spec 1 §7). Revisit only if that stops being true.
- **No metrics or tracing.** Structured logging only, per §10.10. Instrumentation, if it happens, is Plan 8.
- **No rate limiting.** The bounded queue rejects a burst with `SERVER_BUSY`, which is backpressure, not policy. Per-principal limits belong at the API edge.
