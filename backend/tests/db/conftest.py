"""Fixtures for the integration suite.

Isolation is TRUNCATE between tests, not an outer transaction rolled back.
Several tests here need two connections to observe each other's committed
work — the optimistic append check and `FOR SHARE` selection are precisely
about cross-transaction visibility — and a wrapping transaction would make
those tests silently meaningless.

Nothing here is `autouse`, and `pytestmark` in a conftest does NOT propagate
to test modules. Both facts matter: every module in this directory carries its
own `pytestmark = pytest.mark.integration`, and `pytest_collection_modifyitems`
below fails collection if one forgets. Without that, `-m "not integration"`
would deselect the tests but still build the session-scoped engine, and the
"fast lane" would quietly require PostgreSQL.

`engine` is session-scoped (built once, not per test) and asyncpg binds its
connections to the event loop they were created on. So every async test in
this directory, and every async fixture built from `engine` (`clean_db`,
`sessions`, `migrated_schema`), must run on that same session-scoped loop —
declared per-fixture with `loop_scope="session"` and per-module with
`pytest.mark.asyncio(loop_scope="session")` in `pytestmark`, deliberately
narrow rather than a project-wide default so async tests outside this
directory keep pytest-asyncio's normal per-test loop isolation. Forgetting
the module-level mark reproduces the exact "attached to a different loop"
error this suite exists to prevent, so `pytest_collection_modifyitems` fails
collection for that too, the same way it does for a missing `integration`
mark.
"""

import asyncio
import os
from collections.abc import AsyncIterator
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path

import pytest
import pytest_asyncio
from alembic import command
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from triviador.db.engine import create_engine, sessionmaker_for
from triviador.db.models.auth import User
from triviador.db.models.content import Category, Question, QuestionChoice, QuestionNumeric
from triviador.db.models.presets import RulePreset
from triviador.db.repositories.games import GameRepository
from triviador.db.unit_of_work import UnitOfWork
from triviador.domain.game.rules import DEFAULT_RULES
from triviador.domain.ids import GameId, MapId, PlayerId

# Owned here, not by `Settings` (see `triviador.config`): `Settings.database_url`
# has no default precisely so that an unset `TRIVIADOR_DATABASE_URL` fails loudly
# instead of silently targeting a database — including this one. The test suite's
# own default database is a test-suite concern, not something the production
# config type should carry.
TEST_DATABASE_URL = "postgresql+asyncpg://triviador:triviador@127.0.0.1:5433/triviador_test"

DATABASE_URL = os.environ.get("TRIVIADOR_TEST_DATABASE_URL", TEST_DATABASE_URL)

THIS_DIR = Path(__file__).parent
BACKEND_DIR = THIS_DIR.parent.parent
ALEMBIC_INI = BACKEND_DIR / "alembic.ini"

# `test_security.py` (Plan 5) exercises argon2 and `secrets` only — it opens
# no session, builds no engine, and needs no fixture from this file. Every
# other module here earns the `integration` mark by depending on the
# session-scoped `engine`; this is the one named exception, not a loophole,
# so it stays an explicit allowlist rather than a heuristic ("no `async def`
# test") that could silently swallow a real integration test later.
NO_DATABASE_MODULES = frozenset({THIS_DIR / "test_security.py"})


async def _seed_category(
    sessionmaker: async_sessionmaker[AsyncSession],
    category_id: str = "cat-1",
    *,
    slug: str = "general",
    name: str = "General",
) -> None:
    """Shared with `tests/db/test_question_bank.py` and
    `tests/runtime/integration/conftest.py` — lifted here rather than
    kept as a private helper of one test module, so the seeding never
    has to be reimplemented (and inevitably drift) for a second caller."""
    async with sessionmaker() as session:
        session.add(Category(id=category_id, slug=slug, name=name))
        await session.commit()


async def _seed_user(sessionmaker: async_sessionmaker[AsyncSession], user_id: str) -> None:
    async with sessionmaker() as session:
        session.add(
            User(
                id=user_id,
                username=user_id,
                password_hash="hash",
                display_name=user_id,
                role="admin",
            )
        )
        await session.commit()


async def _seed_mc_question(
    sessionmaker: async_sessionmaker[AsyncSession],
    question_id: str,
    *,
    category_id: str = "cat-1",
    is_active: bool = True,
    prompt: str = "prompt",
    difficulty: str = "easy",
    version: int = 1,
    media_asset_id: str | None = None,
    choices: tuple[tuple[str, bool, str | None], ...] = (
        ("A", False, None),
        ("B", True, None),
    ),
) -> None:
    async with sessionmaker() as session:
        session.add(
            Question(
                id=question_id,
                version=version,
                kind="multiple_choice",
                prompt=prompt,
                category_id=category_id,
                difficulty=difficulty,
                media_asset_id=media_asset_id,
                is_active=is_active,
                prompt_hash=f"hash-{question_id}",
            )
        )
        for idx, (choice_text, is_correct, choice_media_asset_id) in enumerate(choices):
            session.add(
                QuestionChoice(
                    question_id=question_id,
                    idx=idx,
                    text=choice_text,
                    is_correct=is_correct,
                    media_asset_id=choice_media_asset_id,
                )
            )
        await session.commit()


async def _seed_numeric_question(
    sessionmaker: async_sessionmaker[AsyncSession],
    question_id: str,
    *,
    category_id: str = "cat-1",
    is_active: bool = True,
    prompt: str = "how many?",
    difficulty: str = "medium",
    version: int = 1,
    correct_value: Decimal = Decimal("42.5"),
    unit: str | None = "km",
    with_numeric_row: bool = True,
) -> None:
    """`with_numeric_row=False` seeds the bare `questions` row with
    `kind='numeric'` but no matching `question_numeric` row — the malformed
    shape `_materialize` must catch (F4): a row that passes `_select_kind`'s
    count check but has no child row for `_materialize` to read."""
    async with sessionmaker() as session:
        session.add(
            Question(
                id=question_id,
                version=version,
                kind="numeric",
                prompt=prompt,
                category_id=category_id,
                difficulty=difficulty,
                is_active=is_active,
                prompt_hash=f"hash-{question_id}",
            )
        )
        if with_numeric_row:
            session.add(
                QuestionNumeric(question_id=question_id, correct_value=correct_value, unit=unit)
            )
        await session.commit()


def alembic_config(url: str) -> Config:
    """Build a `Config` pointed at this repo's `alembic.ini`, with `sqlalchemy.url`
    overridden to `url`. `alembic.ini` deliberately carries no URL of its own (see
    Task 3's report), so every caller — the CLI via `env.py`'s `Settings()` fallback,
    or a test here — has to supply one explicitly."""
    cfg = Config(str(ALEMBIC_INI))
    cfg.set_main_option("sqlalchemy.url", url)
    return cfg


async def _run_upgrade_head(url: str) -> None:
    """`command.upgrade` ends up calling `env.py`'s `asyncio.run(...)`, which cannot
    be invoked from within a running event loop — so it runs on its own thread."""
    await asyncio.to_thread(command.upgrade, alembic_config(url), "head")


def _lacks_session_loop_scope(item: pytest.Item) -> bool:
    """True for an async test item that hasn't opted into the session loop.

    `asyncio_mode = "auto"` implicitly attaches an `asyncio` marker to every
    async test with empty kwargs; a module that adds
    `pytest.mark.asyncio(loop_scope="session")` to its `pytestmark` overrides
    that with `kwargs={"loop_scope": "session"}`. A sync test item carries no
    `asyncio` marker at all and has no loop to be scoped, so it is exempt.
    """
    marker = item.get_closest_marker("asyncio")
    return marker is not None and marker.kwargs.get("loop_scope") != "session"


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    """Every test module under tests/db must be marked `integration`, and
    every async test here must run on the session-scoped loop `engine`
    (and everything built from it) requires — except the modules named in
    `NO_DATABASE_MODULES`, which need neither.

    A conftest.py hook is registered for the whole pytest session once it is
    loaded, not scoped to its own directory — `items` here is every item
    collected anywhere under `testpaths`, not just this directory's. So both
    checks below filter to items whose file lives under `tests/db` itself;
    without that filter, this hook would reject the entire fast lane the
    moment collection touches this directory.
    """
    db_items = [
        item
        for item in items
        if item.path.is_relative_to(THIS_DIR) and item.path not in NO_DATABASE_MODULES
    ]

    unmarked = sorted(
        {item.nodeid.split("::")[0] for item in db_items if "integration" not in item.keywords}
    )
    if unmarked:
        raise pytest.UsageError(
            "tests/db modules must declare `pytestmark = pytest.mark.integration`; "
            "missing in: " + ", ".join(unmarked)
        )

    missing_loop_scope = sorted(
        {item.nodeid.split("::")[0] for item in db_items if _lacks_session_loop_scope(item)}
    )
    if missing_loop_scope:
        raise pytest.UsageError(
            "tests/db async tests use fixtures built from the session-scoped "
            '`engine` and must carry `pytest.mark.asyncio(loop_scope="session")` '
            "— either in the module's `pytestmark`, or per test where the module "
            "also holds synchronous tests (a module-level asyncio mark lands on "
            "those too, and pytest-asyncio warns about it). Missing in: "
            + ", ".join(missing_loop_scope)
        )


UNREACHABLE = (
    f"Cannot reach the test database at {DATABASE_URL}.\n"
    "Start it with:  docker compose -f docker-compose.test.yml up -d\n"
    "These tests fail rather than skip: a silently skipped integration suite "
    "reports green while proving nothing."
)


async def wait_until_a_backend_is_blocked_on(
    sessionmaker: async_sessionmaker[AsyncSession], relation: str, *, timeout_s: float = 5.0
) -> None:
    """Poll `pg_locks` from a third connection until Postgres itself reports
    some backend genuinely blocked on a lock while running a statement that
    mentions `relation` by name (e.g. `"games"`, `"questions"`).

    Shared by `tests/db/test_event_store.py` and `tests/db/test_question_bank.py`
    — both previously carried byte-identical copies of this helper.

    A plain `asyncio.Event` signalled right before a conflicting statement is
    issued is *not* sufficient on its own to guarantee that statement has
    actually reached the database and started blocking by the time the first
    side resumes and commits: `session.begin()` and `SET LOCAL` each cross
    an await boundary of their own, and empirically (see Task 6's report)
    the first side's commit can land before the second side's conflicting
    statement is even dispatched, let alone blocked. Asking Postgres
    directly — rather than inferring "the second side must be blocked by
    now" from Python-side event ordering — is what makes the contention
    itself deterministic.

    Scoped to `relation`, not cluster-wide `pg_stat_activity` filtered only
    on `wait_event_type = 'Lock'`: the latter is satisfied by *any* backend
    blocked on *any* lock anywhere in the cluster — including an autovacuum
    worker, or (were this suite ever run under `pytest-xdist`) a completely
    unrelated test in another worker process — which could return early
    without the specific contention this test is trying to observe having
    happened at all.

    This does **not** filter on `pg_locks.relation` directly (verified: it
    doesn't work). Both contention shapes these tests produce — a second
    `UPDATE games SET last_seq = ...` racing the row `append` already
    updated, and a second `SELECT ... FOR UPDATE` racing a row already
    locked `FOR SHARE` — block on a *tuple*/`transactionid` wait, not a
    relation-level lock: the relation-level intent lock (`RowShareLock` /
    `RowExclusiveLock`) is granted to both sides immediately, since it does
    not itself conflict, and only the specific-row wait is left ungranted,
    recorded with `locktype = 'transactionid'` and `relation` NULL.
    Confirmed against a live contended pair before shipping this — see the
    task report. So this joins `pg_locks` (`NOT granted`) to
    `pg_stat_activity` on `pid` and matches the blocked backend's own
    in-flight `query` text against `relation` instead: scoped to "a backend
    blocked while operating on this table," which is what the `relation`
    parameter is actually trying to express, achieved by a route that
    empirically fires.

    The genuinely load-bearing property is not "this never returns early" —
    it is that **a premature return here cannot produce a false green.** If
    it does fire early, the caller's contention degenerates back toward the
    pre-barrier ~1-in-4 rate at which the two sides happen to interleave
    without the barrier's help (see Task 6's report). But the contended and
    uncontended paths converge on the same observable outcome — `UPDATE ...
    WHERE last_seq = :expected` matching zero rows and raising
    `ConcurrentModification`, or a `FOR UPDATE` probe blocking until the
    holder's transaction ends — so a genuinely broken `append` or a broken
    `FOR SHARE` still fails the calling test either way. This barrier
    sharpens *which mechanism* a passing run demonstrates; it is not what
    the test's assertions depend on to catch a real bug.

    Every iteration here is a real round trip to the database, not a
    timing-based wait, and `timeout_s` is a safety bound against a stuck
    test hanging forever on a real bug, not the synchronization mechanism
    itself.
    """
    loop = asyncio.get_event_loop()
    deadline = loop.time() + timeout_s
    async with sessionmaker() as session:
        while True:
            count = (
                await session.execute(
                    text(
                        "SELECT count(*) FROM pg_locks l "
                        "JOIN pg_stat_activity a ON a.pid = l.pid "
                        "WHERE NOT l.granted AND a.query ILIKE '%' || :relation || '%'"
                    ),
                    {"relation": relation},
                )
            ).scalar_one()
            if count > 0:
                return
            if loop.time() > deadline:
                raise AssertionError(
                    f"timed out waiting for a backend to block on a lock against {relation!r}"
                )


@pytest_asyncio.fixture(scope="session", loop_scope="session")
async def engine() -> AsyncIterator[AsyncEngine]:
    eng = create_engine(DATABASE_URL)
    try:
        async with eng.connect() as conn:
            await conn.execute(text("SELECT 1"))
    except Exception as exc:  # re-raised as a usable message via pytest.fail
        await eng.dispose()
        pytest.fail(f"{UNREACHABLE}\n\nunderlying error: {exc!r}")
    yield eng
    await eng.dispose()


@pytest_asyncio.fixture(scope="session", loop_scope="session")
async def migrated_schema(engine: AsyncEngine) -> None:
    """Build the schema exactly once per session, by running `alembic upgrade
    head` — never `Base.metadata.create_all`. Using the migration is what
    keeps `alembic check` (models vs. migrations) meaningful: if tests built
    the schema some other way, that check and the tests would be exercising
    two different schemas, and a migration bug could only be found in
    production.
    """
    # Two statements, not one: asyncpg's prepared-statement protocol rejects
    # multiple commands in a single `execute()` call.
    async with engine.begin() as conn:
        await conn.execute(text("DROP SCHEMA public CASCADE"))
        await conn.execute(text("CREATE SCHEMA public"))
    await _run_upgrade_head(DATABASE_URL)


@pytest_asyncio.fixture(loop_scope="session")
async def clean_db(migrated_schema: None, engine: AsyncEngine) -> AsyncIterator[None]:
    # Truncate BEFORE the test runs, not after: truncating on the way out
    # leaves the database dirty for any test that does not itself request
    # this fixture, and that dirt would surface as failures unrelated to
    # whatever ran next.
    async with engine.begin() as conn:
        await conn.execute(
            text(
                "TRUNCATE TABLE game_events, game_players, games, question_imports, "
                "question_choices, question_numeric, questions, categories, "
                "media_assets, rule_presets, sessions, invite_codes, users "
                "RESTART IDENTITY CASCADE"
            )
        )
    yield


@pytest_asyncio.fixture(loop_scope="session")
async def sessions(migrated_schema: None, engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return sessionmaker_for(engine)


@pytest_asyncio.fixture(loop_scope="session")
async def default_preset(clean_db: None, sessions: async_sessionmaker[AsyncSession]) -> None:
    """Restore migration 0002's row after `clean_db` has truncated it.

    Depends on `clean_db` rather than replacing it: the point is a known
    baseline *before every test*, not surviving state. A test that
    deactivates the default gets a fresh active one next time, and nothing
    depends on the order tests happen to run in.

    The row is inserted from the same frozen literal (`DEFAULT_PRESET_RULES`)
    migration 0002 seeds from, so the *values* here cannot drift from what
    that migration intends to write. That is not the same guarantee as "this
    fixture proves the migration wrote a usable row" — it inserts through the
    ORM directly, bypassing `upgrade()`'s own SQL entirely, so a bug in how
    that SQL encodes `rules` (there was one: see
    `test_the_default_preset_migration_writes_a_readable_object` in
    `tests/db/test_migrations.py`) would still pass every test built on this
    fixture. Whether `upgrade()` itself produces a row `PresetRepository` can
    read is that other test's job, not this fixture's.
    """
    from triviador.db.seed import DEFAULT_PRESET_RULES

    async with sessions() as session, session.begin():
        session.add(
            RulePreset(
                id="default",
                name="Default",
                is_default=True,
                rules=dict(DEFAULT_PRESET_RULES),
                version=1,
                is_active=True,
            )
        )


@dataclass(frozen=True)
class LobbyGame:
    """What `tests/db/test_reconciliation.py` needs to drive
    `operation_matches` without re-deriving `GameRepository.create`'s
    genesis dance itself: a game id already sitting at `last_seq=1`, and a
    `UnitOfWork` bound to the same `sessionmaker` that created it."""

    game_id: GameId
    uow: UnitOfWork


@pytest_asyncio.fixture(loop_scope="session")
async def lobby_game(clean_db: None, sessions: async_sessionmaker[AsyncSession]) -> LobbyGame:
    """One `games` row at `last_seq=1`, built the same way
    `tests/db/test_event_store.py`'s
    `test_create_then_append_reads_back_as_a_two_event_stream` builds it:
    a seeded host user, then `GameRepository.create`'s genesis transaction
    (`INSERT games` + the seq-1 `GameCreated` row). `append`'s optimistic
    check has nothing to match before that row exists, so every
    reconciliation test needs this seam crossed first, not `_seed_game`'s
    direct insert — genesis is exactly what `operation_matches` callers
    reconcile *after*, not a detail to bypass.

    Also seeds `p1` and `p2`: reconciliation tests append `PlayerJoined`
    batches naming those ids, and `_project` inserts a `game_players` row
    with a `user_id` foreign key to `users.id` for each one.
    """
    async with sessions() as session:
        for user_id in ("host", "p1", "p2"):
            session.add(
                User(
                    id=user_id,
                    username=user_id,
                    password_hash="hash",
                    display_name=user_id,
                    role="player",
                )
            )
        await session.commit()

    game_id = GameId("lobby-game")
    repo = GameRepository(sessions)
    await repo.create(
        game_id=game_id,
        map_id=MapId("m1"),
        rules=DEFAULT_RULES,
        host_id=PlayerId("host"),
        map_sha256="1" * 64,
        preset_id=None,
        operation_id="op-create",
    )

    return LobbyGame(game_id=game_id, uow=UnitOfWork(sessions))
