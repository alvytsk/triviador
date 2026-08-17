"""Wires the real adapters onto `tests/db`'s database fixtures.

Everything under here is the runtime running against PostgreSQL: a real
`UnitOfWork`, a real `GameRepository`, a real `QuestionBank`, a real map
on disk with a real digest. Only the clock, the broadcaster and the
subscriber control stay fake — the first because §12.2 forbids waiting on
wall-clock time, the other two because they are Plan 5's.
"""

import asyncio
import json
import random
from datetime import UTC, datetime
from pathlib import Path

import pytest
import pytest_asyncio
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tests.conftest import grid_map

# Re-exported so this directory can use them: conftest fixtures do not
# reach sideways across sibling directories.
from tests.db.conftest import (  # noqa: F401
    _lacks_session_loop_scope,
    _seed_category,
    _seed_mc_question,
    _seed_numeric_question,
    _seed_user,
    clean_db,
    engine,
    migrated_schema,
    sessions,
)
from tests.runtime.fakes import FakeBroadcaster, FakeClock, FakeSubscribers, RecordingOrigin
from triviador.db.models.content import Question
from triviador.db.models.games import Game, GameEventRow, GamePlayer
from triviador.db.repositories.games import GameRepository
from triviador.db.unit_of_work import UnitOfWork
from triviador.domain.game.actions import Command
from triviador.domain.game.rules import DEFAULT_RULES
from triviador.domain.ids import GameId, MapId, PlayerId
from triviador.maps.registry import MapRegistry
from triviador.runtime.commit import CommandExecutor
from triviador.runtime.loader import GameLoader
from triviador.runtime.manager import GameManager
from triviador.runtime.materialiser import Materialiser
from triviador.runtime.runtime import GameRuntime, QueuedCommand
from triviador.services.ports import UnitOfWorkPort

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


# --------------------------------------------------------------------------
# seeding: users, the question bank, and a real map.json on disk
# --------------------------------------------------------------------------


async def seed_user(sessionmaker: async_sessionmaker[AsyncSession], user_id: str) -> None:
    """A thin re-export under this directory's own name — `_seed_user`
    lives in `tests/db/conftest.py`, shared rather than reimplemented."""
    await _seed_user(sessionmaker, user_id)


async def seed_question_bank(
    sessionmaker: async_sessionmaker[AsyncSession], *, numeric: int, multiple_choice: int
) -> None:
    """One category, `numeric` numeric questions and `multiple_choice`
    multiple-choice questions — a loop over the same `_seed_numeric_question`
    / `_seed_mc_question` helpers `tests/db/test_question_bank.py` already
    gets right, including `prompt_hash` and the `question_numeric` child
    row.
    """
    await _seed_category(sessionmaker)
    for i in range(numeric):
        await _seed_numeric_question(sessionmaker, f"num-{i}")
    for i in range(multiple_choice):
        await _seed_mc_question(sessionmaker, f"mc-{i}")


def write_grid_map(map_dir: Path) -> None:
    """Serialize `tests/conftest.grid_map()` into the JSON shape
    `MapRegistry.load_with_digest` parses (`maps/registry.py`): a
    `map_id`, a `regions` list of `{"id", "name"}`, and an `adjacency`
    mapping of region id to a list of neighbour ids.

    `map_dir` is the map's own directory (e.g. `<root>/grid`), matching
    `MapRegistry.load_with_digest`'s `root / map_id / "map.json"`.
    """
    defn = grid_map()
    raw = {
        "map_id": str(defn.map_id),
        "regions": [{"id": str(r.region_id), "name": r.display_name} for r in defn.regions],
        "adjacency": {
            str(region_id): sorted(str(n) for n in neighbours)
            for region_id, neighbours in defn.adjacency.items()
        },
    }
    map_dir.mkdir(parents=True, exist_ok=True)
    (map_dir / "map.json").write_text(json.dumps(raw), encoding="utf-8")


def rewrite_map_adding_a_region(map_dir: Path) -> None:
    """Add one more region to an already-written `map.json`, wired in
    (symmetrically, to a real neighbour) so the result still passes
    `validate_map` — connected, symmetric adjacency, no self-loop — while
    changing `canonical_digest`'s input and therefore `map_sha256`.

    Every region id logged against the old map may now name a different
    region under the new one; recovery must refuse outright rather than
    fold against the wrong adjacency, which is exactly what
    `test_a_map_digest_mismatch_makes_the_game_unrecoverable` checks.
    """
    path = map_dir / "map.json"
    raw = json.loads(path.read_text(encoding="utf-8"))
    raw["regions"].append({"id": "extra", "name": "EXTRA"})
    raw["adjacency"]["extra"] = ["r0"]
    raw["adjacency"]["r0"] = [*raw["adjacency"]["r0"], "extra"]
    path.write_text(json.dumps(raw), encoding="utf-8")


# --------------------------------------------------------------------------
# fixtures
# --------------------------------------------------------------------------


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
    clean_db: None,  # noqa: F811 — re-binds the imported fixture by name, required for injection
    sessions: async_sessionmaker[AsyncSession],  # noqa: F811 — same reason
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
    manager: GameManager,
    sessions: async_sessionmaker[AsyncSession],  # noqa: F811 — re-binds the imported fixture
    map_root: Path,
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


# --------------------------------------------------------------------------
# runtime seams
# --------------------------------------------------------------------------


async def drain_runtime(runtime: GameRuntime, *, max_turns: int = 200) -> None:
    """Settle the fake clock until the runtime goes idle.

    Bounded, and it raises rather than looping: a wedged consumer must
    fail the test that provoked it, not hang the suite until CI times out
    with no indication of which test was responsible.

    Against real PostgreSQL this needs one thing the fakes never did: a
    real, tiny sleep between polls. `FakeClock.settle()`'s three
    zero-delay `asyncio.sleep(0)` yields were tuned for the fully
    in-memory fakes (Task 2), where a "settle" only ever has to let an
    already-resolved chain of Python callbacks run — nothing is actually
    waiting on the wire. Against asyncpg, the in-flight command's
    consumer task is genuinely parked on the driver's own future
    (`BaseProtocol._on_waiter_completed`) until the real network round
    trip to PostgreSQL completes, and a zero-delay poll — however many
    times it is repeated — never gives the event loop enough real elapsed
    time to observe that response arrive: confirmed by instrumenting a
    live run, where the consumer sat "in_flight" through all 200 turns of
    a pure `clock.settle()` loop and resolved within about four turns the
    moment a real `asyncio.sleep` was interleaved. The 1ms sleep below is
    not simulated game time — nothing here is guessing how long a
    deadline takes, which is what §12.2 actually forbids — it is letting
    a real, already-in-flight I/O call finish; `max_turns` still bounds
    the wait and this still raises rather than hanging.
    """
    clock = runtime.clock
    assert isinstance(clock, FakeClock)
    for _ in range(max_turns):
        await clock.settle()
        if runtime.is_idle():
            return
        await asyncio.sleep(0.001)
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
    shared; everything in memory is new.

    `old._uow` and `old._loader` are stateless wrappers around the
    sessionmaker and the map registry — every `begin()`/`load()` call
    opens its own connection, so reusing the objects is equivalent to
    rebuilding them and carries none of the manager's own in-memory state
    (`_entries`, `_locks`, `_quarantines`, the generation counter) forward.
    That state is what a restart actually erases, and a brand-new
    `GameManager` is exactly that: nothing here is `old`'s registry. The
    broadcaster and subscriber control are genuinely in-memory — Plan 5's
    WebSocket hub does not survive a restart either — so those get fresh
    instances, and the materialiser gets a freshly seeded rng the same way
    a real process boot would.
    """
    return GameManager(
        loader=old._loader,
        uow=old._uow,
        materialiser=Materialiser(clock=old._clock, rng=random.Random(1234)),
        clock=old._clock,
        broadcaster=FakeBroadcaster(),
        subscribers=FakeSubscribers(),
        games=old._games,
        rng=random.Random(1234),
        queue_maxsize=old._queue_maxsize,
        commit_max_attempts=old._commit_max_attempts,
        backoff_initial_s=old._backoff_initial_s,
        backoff_max_s=old._backoff_max_s,
    )


def executor_over(uow: UnitOfWorkPort, manager: GameManager) -> CommandExecutor:
    """A `CommandExecutor` over `uow`, with `manager`'s own materialiser,
    clock and rng — used to swap `BreakingUnitOfWork` onto a live runtime
    via `replace_executor_for_test` (Task 12's seam) while keeping every
    other collaborator identical to the one the runtime was built with."""
    return CommandExecutor(
        uow=uow,
        materialiser=manager._materialiser,
        clock=manager._clock,
        rng=manager._rng,
        max_attempts=manager._commit_max_attempts,
    )


# --------------------------------------------------------------------------
# query helpers — two to four line SELECTs over db/models, modelled on
# `_get_game` / `_event_rows` in tests/db/test_event_store.py
# --------------------------------------------------------------------------


async def game_status(sessionmaker: async_sessionmaker[AsyncSession], game_id: GameId) -> str:
    async with sessionmaker() as session:
        game = await session.get(Game, game_id)
        assert game is not None
        return game.status


async def last_seq(sessionmaker: async_sessionmaker[AsyncSession], game_id: GameId) -> int:
    async with sessionmaker() as session:
        game = await session.get(Game, game_id)
        assert game is not None
        return game.last_seq


async def event_row_count(sessionmaker: async_sessionmaker[AsyncSession], game_id: GameId) -> int:
    async with sessionmaker() as session:
        result = await session.execute(
            select(func.count()).select_from(GameEventRow).where(GameEventRow.game_id == game_id)
        )
        return result.scalar_one()


async def event_seqs(sessionmaker: async_sessionmaker[AsyncSession], game_id: GameId) -> list[int]:
    async with sessionmaker() as session:
        result = await session.execute(
            select(GameEventRow.seq)
            .where(GameEventRow.game_id == game_id)
            .order_by(GameEventRow.seq)
        )
        return list(result.scalars().all())


async def player_seats(
    sessionmaker: async_sessionmaker[AsyncSession], game_id: GameId
) -> dict[str, int]:
    async with sessionmaker() as session:
        result = await session.execute(
            select(GamePlayer.user_id, GamePlayer.seat).where(GamePlayer.game_id == game_id)
        )
        return {user_id: seat for user_id, seat in result.all()}


async def deactivate_all_questions(sessionmaker: async_sessionmaker[AsyncSession]) -> None:
    """§10.6's checkpoint: drain the bank so the next `StartGame`'s draw
    is authoritatively rejected."""
    async with sessionmaker() as session:
        await session.execute(update(Question).values(is_active=False))
        await session.commit()


async def rewrite_every_question_prompt(
    sessionmaker: async_sessionmaker[AsyncSession], prefix: str
) -> None:
    """§12.2's pool-immutability rewrite: every `questions.prompt` gets
    `prefix` prepended, in place, after the pool the game will replay
    against has already been drawn and committed."""
    async with sessionmaker() as session:
        await session.execute(update(Question).values(prompt=func.concat(prefix, Question.prompt)))
        await session.commit()
