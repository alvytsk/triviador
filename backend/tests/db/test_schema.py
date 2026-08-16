"""The schema as actually built in PostgreSQL, not as SQLAlchemy metadata says it is.

Every assertion here queries `information_schema` / `pg_catalog` against the live
database produced by `alembic upgrade head` (via the `migrated_schema` fixture).
Introspecting `Base.metadata` instead would only prove the models agree with
themselves — it would pass even if the migration that is supposed to build the
same schema in Postgres silently dropped a constraint. That is exactly the
failure autogenerate is prone to (partial indexes, CHECK bodies), which is why
this module exists as an independent check on the migration, not on the models.

Checks are data-driven: a table of `(table, columns, ...)` per constraint kind,
so a missing constraint names itself (`games: expected PK('id',), got PK()`)
instead of producing an opaque `assert False`.
"""

import re
from collections.abc import Mapping
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="session")]


# --------------------------------------------------------------------------
# information_schema / pg_catalog helpers
# --------------------------------------------------------------------------


async def _pk_columns(conn: AsyncConnection, table: str) -> list[str]:
    rows = await conn.execute(
        text(
            """
            SELECT kcu.column_name
            FROM information_schema.table_constraints tc
            JOIN information_schema.key_column_usage kcu
              ON tc.constraint_name = kcu.constraint_name
             AND tc.table_schema = kcu.table_schema
            WHERE tc.table_schema = 'public'
              AND tc.table_name = :table
              AND tc.constraint_type = 'PRIMARY KEY'
            ORDER BY kcu.ordinal_position
            """
        ),
        {"table": table},
    )
    return [row[0] for row in rows]


async def _unique_constraint_column_sets(conn: AsyncConnection, table: str) -> list[frozenset[str]]:
    rows = await conn.execute(
        text(
            """
            SELECT tc.constraint_name, kcu.column_name
            FROM information_schema.table_constraints tc
            JOIN information_schema.key_column_usage kcu
              ON tc.constraint_name = kcu.constraint_name
             AND tc.table_schema = kcu.table_schema
            WHERE tc.table_schema = 'public'
              AND tc.table_name = :table
              AND tc.constraint_type = 'UNIQUE'
            ORDER BY tc.constraint_name, kcu.ordinal_position
            """
        ),
        {"table": table},
    )
    groups: dict[str, set[str]] = {}
    for name, column in rows:
        groups.setdefault(name, set()).add(column)
    return [frozenset(cols) for cols in groups.values()]


async def _column(conn: AsyncConnection, table: str, column: str) -> Mapping[str, Any] | None:
    rows = await conn.execute(
        text(
            """
            SELECT is_nullable, data_type, column_default
            FROM information_schema.columns
            WHERE table_schema = 'public' AND table_name = :table AND column_name = :column
            """
        ),
        {"table": table, "column": column},
    )
    row = rows.first()
    return dict(row._mapping) if row is not None else None


async def _check_constraint_defs(conn: AsyncConnection, table: str) -> dict[str, str]:
    rows = await conn.execute(
        text(
            """
            SELECT c.conname, pg_get_constraintdef(c.oid)
            FROM pg_constraint c
            JOIN pg_class t ON c.conrelid = t.oid
            JOIN pg_namespace n ON t.relnamespace = n.oid
            WHERE n.nspname = 'public' AND t.relname = :table AND c.contype = 'c'
            """
        ),
        {"table": table},
    )
    return {name: definition for name, definition in rows}


async def _indexes(conn: AsyncConnection, table: str) -> dict[str, str]:
    rows = await conn.execute(
        text(
            "SELECT indexname, indexdef FROM pg_indexes "
            "WHERE schemaname = 'public' AND tablename = :table"
        ),
        {"table": table},
    )
    return {name: indexdef for name, indexdef in rows}


async def _foreign_key(conn: AsyncConnection, table: str, column: str) -> tuple[str, str] | None:
    rows = await conn.execute(
        text(
            """
            SELECT ccu.table_name, ccu.column_name
            FROM information_schema.table_constraints tc
            JOIN information_schema.key_column_usage kcu
              ON tc.constraint_name = kcu.constraint_name AND tc.table_schema = kcu.table_schema
            JOIN information_schema.constraint_column_usage ccu
              ON tc.constraint_name = ccu.constraint_name AND tc.table_schema = ccu.table_schema
            WHERE tc.constraint_type = 'FOREIGN KEY'
              AND tc.table_schema = 'public'
              AND tc.table_name = :table
              AND kcu.column_name = :column
            """
        ),
        {"table": table, "column": column},
    )
    row = rows.first()
    return (row[0], row[1]) if row is not None else None


# --------------------------------------------------------------------------
# Primary keys
# --------------------------------------------------------------------------

PRIMARY_KEYS: list[tuple[str, tuple[str, ...]]] = [
    ("games", ("id",)),
    ("game_players", ("game_id", "user_id")),
    ("game_events", ("game_id", "seq")),
    ("rule_presets", ("id",)),
    ("questions", ("id",)),
    ("question_choices", ("question_id", "idx")),
    ("question_numeric", ("question_id",)),
    ("media_assets", ("id",)),
    ("invite_codes", ("id",)),
    ("users", ("id",)),
    ("sessions", ("id",)),
    ("categories", ("id",)),
    ("question_imports", ("id",)),
]


@pytest.mark.parametrize("table,columns", PRIMARY_KEYS, ids=[t for t, _ in PRIMARY_KEYS])
async def test_primary_key(
    migrated_schema: None, engine: AsyncEngine, table: str, columns: tuple[str, ...]
) -> None:
    async with engine.connect() as conn:
        actual = await _pk_columns(conn, table)
    assert actual == list(columns), f"{table}: expected PK{columns}, got PK{tuple(actual)}"


# --------------------------------------------------------------------------
# NOT NULL columns
# --------------------------------------------------------------------------

NOT_NULL_COLUMNS: list[tuple[str, str]] = [
    ("games", "status"),
    ("games", "last_seq"),
    ("games", "rules"),
    ("game_events", "schema_version"),
    ("game_events", "payload"),
    ("rule_presets", "is_active"),
    ("questions", "version"),
    ("questions", "is_active"),
    ("questions", "prompt_hash"),
    ("question_numeric", "correct_value"),
    ("question_imports", "status"),
]


@pytest.mark.parametrize(
    "table,column", NOT_NULL_COLUMNS, ids=[f"{t}.{c}" for t, c in NOT_NULL_COLUMNS]
)
async def test_not_null(
    migrated_schema: None, engine: AsyncEngine, table: str, column: str
) -> None:
    async with engine.connect() as conn:
        col = await _column(conn, table, column)
    assert col is not None, f"{table}.{column} does not exist"
    assert col["is_nullable"] == "NO", f"{table}.{column} expected NOT NULL, is nullable"


# --------------------------------------------------------------------------
# JSONB columns
# --------------------------------------------------------------------------

JSONB_COLUMNS: list[tuple[str, str]] = [
    ("games", "rules"),
    ("game_events", "payload"),
    ("rule_presets", "rules"),
    ("question_imports", "report"),
]


@pytest.mark.parametrize("table,column", JSONB_COLUMNS, ids=[f"{t}.{c}" for t, c in JSONB_COLUMNS])
async def test_column_is_jsonb(
    migrated_schema: None, engine: AsyncEngine, table: str, column: str
) -> None:
    async with engine.connect() as conn:
        col = await _column(conn, table, column)
    assert col is not None, f"{table}.{column} does not exist"
    assert col["data_type"] == "jsonb", f"{table}.{column} expected jsonb, got {col['data_type']}"


# --------------------------------------------------------------------------
# NUMERIC columns
# --------------------------------------------------------------------------

# A deferred finding from Task 3: this file's mandate is to verify the schema
# against live PostgreSQL rather than the models, the same way
# `test_column_is_jsonb` does above — but `question_numeric.correct_value`'s
# NOT NULL was checked (see NOT_NULL_COLUMNS) without ever checking that the
# column is actually `numeric` in the database. A `float`/`double precision`
# column would satisfy every check above while silently corrupting every
# numeric answer it stores.
NUMERIC_COLUMNS: list[tuple[str, str]] = [
    ("question_numeric", "correct_value"),
]


@pytest.mark.parametrize(
    "table,column", NUMERIC_COLUMNS, ids=[f"{t}.{c}" for t, c in NUMERIC_COLUMNS]
)
async def test_column_is_numeric(
    migrated_schema: None, engine: AsyncEngine, table: str, column: str
) -> None:
    async with engine.connect() as conn:
        col = await _column(conn, table, column)
    assert col is not None, f"{table}.{column} does not exist"
    assert col["data_type"] == "numeric", (
        f"{table}.{column} expected numeric, got {col['data_type']}"
    )


# --------------------------------------------------------------------------
# UNIQUE constraints (plain, not partial)
# --------------------------------------------------------------------------

UNIQUE_CONSTRAINTS: list[tuple[str, tuple[str, ...]]] = [
    ("game_players", ("game_id", "seat")),
    ("invite_codes", ("code_hash",)),
    ("users", ("username",)),
    ("sessions", ("token_hash",)),
]


@pytest.mark.parametrize(
    "table,columns", UNIQUE_CONSTRAINTS, ids=[f"{t}({','.join(c)})" for t, c in UNIQUE_CONSTRAINTS]
)
async def test_unique_constraint(
    migrated_schema: None, engine: AsyncEngine, table: str, columns: tuple[str, ...]
) -> None:
    async with engine.connect() as conn:
        actual = await _unique_constraint_column_sets(conn, table)
    expected = frozenset(columns)
    assert expected in actual, f"{table}: expected UNIQUE{columns}, found unique sets {actual}"


# --------------------------------------------------------------------------
# CHECK constraints over a closed set of literal values
# --------------------------------------------------------------------------

CHECK_CONSTRAINT_VALUES: list[tuple[str, str, frozenset[str]]] = [
    ("games", "status", frozenset({"lobby", "expansion", "battle", "finished", "aborted"})),
    ("questions", "kind", frozenset({"multiple_choice", "numeric"})),
    ("questions", "difficulty", frozenset({"easy", "medium", "hard"})),
]


@pytest.mark.parametrize(
    "table,column,expected",
    CHECK_CONSTRAINT_VALUES,
    ids=[f"{t}.{c}" for t, c, _ in CHECK_CONSTRAINT_VALUES],
)
async def test_check_constraint_exact_values(
    migrated_schema: None,
    engine: AsyncEngine,
    table: str,
    column: str,
    expected: frozenset[str],
) -> None:
    async with engine.connect() as conn:
        defs = await _check_constraint_defs(conn, table)
    matching = [d for d in defs.values() if re.search(rf"\b{re.escape(column)}\b", d)]
    assert matching, f"no CHECK constraint on {table}.{column}; constraints found: {defs}"
    actual: set[str] = set()
    for definition in matching:
        actual.update(re.findall(r"'([^']*)'", definition))
    assert actual == set(expected), f"{table}.{column} CHECK values {actual} != expected {expected}"


# --------------------------------------------------------------------------
# One-off structural checks that don't fit the tables above
# --------------------------------------------------------------------------


async def test_game_events_game_id_operation_id_index(
    migrated_schema: None, engine: AsyncEngine
) -> None:
    async with engine.connect() as conn:
        idx = await _indexes(conn, "game_events")
    matching = [d for d in idx.values() if "game_id" in d and "operation_id" in d]
    assert matching, f"expected an index on game_events(game_id, operation_id); found: {idx}"


async def test_rule_presets_single_default_partial_unique_index(
    migrated_schema: None, engine: AsyncEngine
) -> None:
    async with engine.connect() as conn:
        idx = await _indexes(conn, "rule_presets")
    matching = [
        d
        for d in idx.values()
        if "UNIQUE" in d.upper() and "is_default" in d and "WHERE" in d.upper()
    ]
    assert matching, (
        f"expected a partial UNIQUE index on rule_presets(is_default) WHERE is_default; "
        f"found indexes: {idx}"
    )


async def test_question_choices_media_asset_id_fk_nullable(
    migrated_schema: None, engine: AsyncEngine
) -> None:
    async with engine.connect() as conn:
        fk = await _foreign_key(conn, "question_choices", "media_asset_id")
        col = await _column(conn, "question_choices", "media_asset_id")
    assert fk == ("media_assets", "id"), f"expected FK to media_assets(id), got {fk}"
    assert col is not None
    assert col["is_nullable"] == "YES", "question_choices.media_asset_id must be nullable"


async def test_media_assets_id_has_no_default(migrated_schema: None, engine: AsyncEngine) -> None:
    """`id` is the sha256 of the content, supplied by the application — never
    generated by the database."""
    async with engine.connect() as conn:
        col = await _column(conn, "media_assets", "id")
    assert col is not None
    default = col["column_default"]
    assert default is None, f"media_assets.id must have no default, got {default}"


async def test_rule_presets_is_active_not_null_default_true(
    migrated_schema: None, engine: AsyncEngine
) -> None:
    async with engine.connect() as conn:
        col = await _column(conn, "rule_presets", "is_active")
    assert col is not None
    assert col["is_nullable"] == "NO"
    assert col["data_type"] == "boolean"
    default = col["column_default"]
    assert default is not None and "true" in str(default).lower(), (
        f"rule_presets.is_active expected DEFAULT true, got {default!r}"
    )


# --------------------------------------------------------------------------
# Whole-schema: no naive timestamp anywhere
# --------------------------------------------------------------------------


async def test_no_naive_timestamp_columns(migrated_schema: None, engine: AsyncEngine) -> None:
    """This system compares absolute deadlines across process restarts, so a
    `timestamp without time zone` column is a correctness bug, not a style
    issue — one query over the whole schema, not per-table."""
    async with engine.connect() as conn:
        rows = await conn.execute(
            text(
                "SELECT table_name, column_name FROM information_schema.columns "
                "WHERE table_schema = 'public' AND data_type = 'timestamp without time zone'"
            )
        )
        offenders = [f"{table}.{column}" for table, column in rows]
    assert not offenders, f"naive timestamp columns found (must be timestamptz): {offenders}"
