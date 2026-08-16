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
from pathlib import Path

import pytest
import pytest_asyncio
from alembic import command
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from triviador.config import TEST_DATABASE_URL
from triviador.db.engine import create_engine, sessionmaker_for

DATABASE_URL = os.environ.get("TRIVIADOR_TEST_DATABASE_URL", TEST_DATABASE_URL)

THIS_DIR = Path(__file__).parent
BACKEND_DIR = THIS_DIR.parent.parent
ALEMBIC_INI = BACKEND_DIR / "alembic.ini"


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
    (and everything built from it) requires.

    A conftest.py hook is registered for the whole pytest session once it is
    loaded, not scoped to its own directory — `items` here is every item
    collected anywhere under `testpaths`, not just this directory's. So both
    checks below filter to items whose file lives under `tests/db` itself;
    without that filter, this hook would reject the entire fast lane the
    moment collection touches this directory.
    """
    db_items = [item for item in items if item.path.is_relative_to(THIS_DIR)]

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
            "tests/db modules use fixtures built from the session-scoped `engine` "
            "and must declare `pytestmark = [pytest.mark.integration, "
            'pytest.mark.asyncio(loop_scope="session")]`; missing in: '
            + ", ".join(missing_loop_scope)
        )


UNREACHABLE = (
    f"Cannot reach the test database at {DATABASE_URL}.\n"
    "Start it with:  docker compose -f docker-compose.test.yml up -d\n"
    "These tests fail rather than skip: a silently skipped integration suite "
    "reports green while proving nothing."
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
