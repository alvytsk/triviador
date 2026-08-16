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
"""

import os
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from triviador.config import TEST_DATABASE_URL
from triviador.db.engine import create_engine, sessionmaker_for

DATABASE_URL = os.environ.get("TRIVIADOR_TEST_DATABASE_URL", TEST_DATABASE_URL)

THIS_DIR = Path(__file__).parent


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    """Every test module under tests/db must be marked `integration`.

    A conftest.py hook is registered for the whole pytest session once it is
    loaded, not scoped to its own directory — `items` here is every item
    collected anywhere under `testpaths`, not just this directory's. So the
    check below filters to items whose file lives under `tests/db` itself;
    without that filter, this hook would reject the entire fast lane the
    moment collection touches this directory.
    """
    unmarked = sorted(
        {
            item.nodeid.split("::")[0]
            for item in items
            if item.path.is_relative_to(THIS_DIR) and "integration" not in item.keywords
        }
    )
    if unmarked:
        raise pytest.UsageError(
            "tests/db modules must declare `pytestmark = pytest.mark.integration`; "
            "missing in: " + ", ".join(unmarked)
        )


UNREACHABLE = (
    f"Cannot reach the test database at {DATABASE_URL}.\n"
    "Start it with:  docker compose -f docker-compose.test.yml up -d\n"
    "These tests fail rather than skip: a silently skipped integration suite "
    "reports green while proving nothing."
)


@pytest_asyncio.fixture(scope="session")
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


@pytest_asyncio.fixture(scope="session")
async def migrated_schema() -> None:
    """Task 3 replaces this with a real Alembic run against `engine`.

    A no-op for now: the dependency edge below (`clean_db` and `sessions`
    depend on this fixture) needs to exist from the start rather than being
    retrofitted once the schema arrives.
    """
    return None


@pytest_asyncio.fixture
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


@pytest_asyncio.fixture
async def sessions(migrated_schema: None, engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return sessionmaker_for(engine)
