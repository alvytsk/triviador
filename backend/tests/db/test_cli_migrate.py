"""`triviador migrate` — the compose `migrate` service's one job.

`migrate_head` is the part of `_migrate_command` worth testing directly: it
takes `engine` and `database_url` rather than reading `Settings` off the
environment, the same shape `test_migrations.py`'s
`test_upgrade_head_from_empty_database` exercises through the lower-level
`_run_upgrade_head` helper. These tests go one layer up, through the CLI's
own function, so a broken deploy shows up here rather than only in the
migrate service itself.
"""

import pytest
from sqlalchemy import inspect, text
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import AsyncEngine

from tests.db.conftest import DATABASE_URL
from triviador.cli import _MIGRATE_LOCK_KEY, migrate_head

pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="session")]


async def test_migrate_head_upgrades_from_empty_database(engine: AsyncEngine) -> None:
    """Same assertion as `test_migrations.py`'s `test_upgrade_head_from_empty_database`,
    through the CLI's own entry point instead of the bare Alembic API — this is what
    the `migrate` compose service actually runs.

    Leaves the schema at head on exit, so it doesn't matter whether this test runs
    before or after the session-scoped `migrated_schema` fixture is first requested
    elsewhere.
    """
    async with engine.begin() as conn:
        await conn.execute(text("DROP SCHEMA public CASCADE"))
        await conn.execute(text("CREATE SCHEMA public"))

    await migrate_head(engine, DATABASE_URL)

    def _table_names(conn: Connection) -> set[str]:
        return set(inspect(conn).get_table_names())

    async with engine.connect() as conn:
        tables = await conn.run_sync(_table_names)

    assert "games" in tables and "users" in tables


async def test_migrate_head_does_not_leak_the_advisory_lock(
    migrated_schema: None, engine: AsyncEngine
) -> None:
    """A lock held past the command's own lifetime would deadlock every
    future deploy against itself — the exact failure mode the lock exists
    to prevent, self-inflicted. Prove it is released: acquire and release
    it once through `migrate_head` (a no-op upgrade, the schema is already
    at head), then take it again on a fresh connection.
    """
    await migrate_head(engine, DATABASE_URL)

    async with engine.connect() as conn:
        got = await conn.scalar(
            text("SELECT pg_try_advisory_lock(:key)"), {"key": _MIGRATE_LOCK_KEY}
        )
        assert got is True, "advisory lock was still held after migrate_head returned"
        await conn.execute(text("SELECT pg_advisory_unlock(:key)"), {"key": _MIGRATE_LOCK_KEY})
