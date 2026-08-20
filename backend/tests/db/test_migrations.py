"""Migrations are the schema's only constructor.

`test_schema.py` asserts what the schema looks like once built; this module
asserts that the migration is the thing that builds it, that it agrees with
the models (`alembic check`), and that a downgrade does what we've decided it
should do rather than whatever autogenerate stubbed in.

None of these tests leaves the schema torn down on exit: `test_downgrade_is_not_offered`
asserts the downgrade *raises before touching the database*, and
`test_upgrade_head_from_empty_database` re-runs the same upgrade `migrated_schema` runs.
Either way the schema is left at head, so these tests are safe to run before or after
`test_schema.py` regardless of collection order — the session-scoped `migrated_schema`
fixture only ever runs its DROP/CREATE/upgrade once, and nothing here undoes that
after the fact.
"""

import asyncio

import pytest
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import inspect, text
from sqlalchemy.engine import Connection
from sqlalchemy.exc import DBAPIError, ProgrammingError
from sqlalchemy.ext.asyncio import AsyncEngine

from tests.db.conftest import DATABASE_URL, _run_upgrade_head, alembic_config
from triviador.db.base import Base
from triviador.db.engine import sessionmaker_for
from triviador.db.repositories.presets import PresetRepository

pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="session")]

EXPECTED_TABLES = {
    "games",
    "game_players",
    "game_events",
    "rule_presets",
    "questions",
    "question_choices",
    "question_numeric",
    "media_assets",
    "invite_codes",
    "users",
    "sessions",
    "categories",
    "question_imports",
}


async def test_upgrade_head_from_empty_database(engine: AsyncEngine) -> None:
    """Drop everything, run `upgrade head`, assert the expected tables exist.

    Leaves the schema at head on exit — the same state `migrated_schema`
    itself builds — so it doesn't matter whether this test runs before or
    after the session-scoped fixture is first requested elsewhere.
    """
    async with engine.begin() as conn:
        await conn.execute(text("DROP SCHEMA public CASCADE"))
        await conn.execute(text("CREATE SCHEMA public"))

    await _run_upgrade_head(DATABASE_URL)

    def _table_names(conn: Connection) -> set[str]:
        return set(inspect(conn).get_table_names())

    async with engine.connect() as conn:
        tables = await conn.run_sync(_table_names)

    missing = EXPECTED_TABLES - tables
    assert tables >= EXPECTED_TABLES, f"missing tables after upgrade head: {missing}"


async def test_alembic_check_is_clean(migrated_schema: None, engine: AsyncEngine) -> None:
    """Models and migrations agree. This is the gate that catches a model field
    added without a migration — the failure that otherwise surfaces as a
    production `UndefinedColumn` long after the change.

    Uses Alembic's Python API directly against the live test database (the same
    `compare_metadata` machinery the `alembic check` CLI command runs), not a
    subprocess — a subprocess would need its own configuration and could drift
    from what this test itself actually exercises.
    """

    def _diff(conn: Connection) -> list[object]:
        context = MigrationContext.configure(
            conn, opts={"compare_type": True, "compare_server_default": True}
        )
        return list(compare_metadata(context, Base.metadata))

    async with engine.connect() as conn:
        diffs = await conn.run_sync(_diff)

    assert diffs == [], f"models and migrations disagree: {diffs}"


async def test_downgrade_is_not_offered(migrated_schema: None, engine: AsyncEngine) -> None:
    """0001's `downgrade()` deliberately raises rather than dropping the schema.

    This is an event-sourced system: `game_events` is append-only, and no
    later migration may UPDATE or DELETE it. A working `downgrade` here could
    only ever mean "drop every table, including the event log" — there is
    nothing smaller to roll back to once the first migration has run, and
    offering that as a routine, unlabeled `alembic downgrade` invites exactly
    the data loss the append-only discipline exists to prevent. So 0001
    raises `NotImplementedError` instead of shipping autogenerate's silent
    `pass`: a rollback big enough to erase the event log has to be a
    deliberate, manual `DROP SCHEMA`, not a habitual single command.
    """

    def _table_names(conn: Connection) -> set[str]:
        return set(inspect(conn).get_table_names())

    with pytest.raises((NotImplementedError, ProgrammingError, DBAPIError)):
        await asyncio.to_thread(command.downgrade, alembic_config(DATABASE_URL), "base")

    # The raise happens before any DDL, so the schema must be untouched.
    async with engine.connect() as conn:
        tables = await conn.run_sync(_table_names)
    assert tables >= EXPECTED_TABLES, "downgrade must not modify the schema before raising"


async def test_the_default_preset_migration_writes_a_readable_object(engine: AsyncEngine) -> None:
    """Regression test for a double-JSON-encode bug in 0002's `upgrade()`:
    it used to bind `json.dumps(DEFAULT_PRESET_RULES)` — already a string —
    with `type_=sa.JSON`, which serializes it a *second* time. The row's
    `rules` column ended up a JSONB string scalar holding JSON text, not a
    JSONB object, so `PresetRepository.get_default()` raised on every
    freshly migrated database. That call is exactly what `POST /api/games`
    makes when `preset_id` is null, so the bug meant no game could ever be
    created against a fresh deployment.

    Deliberately uses neither `clean_db` nor `default_preset`: the latter
    re-inserts this row through the ORM with a correctly-shaped dict, which
    is precisely what hid this bug for two plans — every other test that
    touched `rule_presets` read that fixture's row, never the migration's
    own. This rebuilds the schema from nothing (the same DROP/CREATE/upgrade
    `test_upgrade_head_from_empty_database` runs) and reads back only what
    `upgrade()` itself wrote.
    """
    async with engine.begin() as conn:
        await conn.execute(text("DROP SCHEMA public CASCADE"))
        await conn.execute(text("CREATE SCHEMA public"))
    await _run_upgrade_head(DATABASE_URL)

    async with engine.connect() as conn:
        kind = (
            await conn.execute(
                text("SELECT jsonb_typeof(rules) FROM rule_presets WHERE id = 'default'")
            )
        ).scalar_one()
    assert kind == "object"

    preset = await PresetRepository(sessionmaker_for(engine)).get_default()
    assert preset is not None
    assert preset.rules.player_count == 3
