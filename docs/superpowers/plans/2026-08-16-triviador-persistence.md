# Triviador Plan 3 — Persistence Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the durable layer the runtime sits on — schema, migrations, the event codec, the event store's optimistic append, the read-model projection written in that same transaction, and the repositories Plan 4 will call. After this plan a game's entire history can be written to PostgreSQL and read back into an identical `GameState`, but nothing yet drives it.

**Architecture:** The event log is the truth; `games` and `game_players` are projections of it maintained inside the same unit of work (ADR-004, Spec 1B §4.2). The domain stays pure: `db/` imports `domain/`, never the reverse, and this plan adds the first mechanized check of that rule.

**Tech Stack:** Python 3.13 · `uv` · SQLAlchemy 2.0 (async, `Mapped[]` style) · `asyncpg` · Alembic · PostgreSQL 17 · Pydantic v2 (codec only) · `ruff` · `mypy --strict` · `pytest` + `pytest-asyncio` · Hypothesis

**Spec:** `docs/superpowers/specs/2026-08-16-triviador-app-architecture-design.md` §4 (schema, read model, codec, append, migrations), §5.1 (the `GameEventStore` / `GameRepository` / `UnitOfWork` / `QuestionBank` ports), §5.3 (one transaction per command, `FOR SHARE` pool selection), §6.2 (genesis commit), §11 (codec test gates) · Spec 1 §7 (base schema), §4 (layering rule)

---

## Global Constraints

Every task's requirements implicitly include this section.

- **The layering rule is now enforceable and enforced.** `domain/` must not import `db/`, `services/`, `api/`, `sqlalchemy`, `asyncpg`, `alembic`, or `pydantic`. Task 1 adds the test that proves it; every later task keeps it green.
- **The event log is append-only.** No `UPDATE` and no `DELETE` on `game_events`, ever, in application code or in a migration.
- **The read model is never written outside the appending transaction.** No asynchronous projector, no backfill job, no repository method that touches `games.last_seq` on its own.
- **Every timestamp column is `TIMESTAMP WITH TIME ZONE`** and every Python `datetime` is timezone-aware UTC. A naive datetime reaching the database is a bug, not a formatting detail — absolute deadlines (ADR-001) are compared across a process restart.
- **Money-like and answer-like numbers are `NUMERIC`, never `float`.** In JSON they are strings. A numeric answer that round-trips through an IEEE double is a wrong answer.
- **The codec targets current dataclasses only.** Decoding runs upcasters forward until the payload matches today's shape; no code path anywhere constructs an old version of an event.
- **Wire names are permanent.** Renaming one is a data migration over `game_events.type`, not an edit. The frozen list in `tests/db/test_wire_names.py` is what makes that a deliberate act.
- **Integration tests run against real PostgreSQL, never SQLite.** `JSONB`, partial unique indexes, `FOR SHARE`, `FOR UPDATE`, and `TIMESTAMPTZ` semantics are the things under test; a substitute engine would test nothing.
- **Integration tests fail loudly when the database is absent.** They must never silently skip — a green suite that quietly ran zero integration tests is the failure mode this rule exists to prevent.
- Python `>=3.13`. Line length 100. `ruff check`, `ruff format --check`, and `mypy --strict` must pass on every commit.
- **`reducer.py` keeps its 100 % branch coverage gate.** Task 1 extends the same gate to `db/codec/`; nothing in this plan may lower either.

---

## File Structure

```
backend/
├── pyproject.toml                 MODIFY  deps, pytest-asyncio, coverage include, mypy excludes
├── alembic.ini                    CREATE
├── docker-compose.test.yml        CREATE  postgres:17 for the integration suite
└── src/triviador/
    ├── config.py                  CREATE  Settings (database_url) via pydantic-settings
    └── db/
        ├── base.py                CREATE  DeclarativeBase + constraint naming convention
        ├── engine.py              CREATE  async engine / sessionmaker factory
        ├── errors.py              CREATE  ConcurrentModification, UnknownEventType, …
        ├── models/
        │   ├── __init__.py        CREATE  re-exports — the one import Alembic autogenerate needs
        │   ├── auth.py            CREATE  users, sessions, invite_codes
        │   ├── content.py         CREATE  categories, questions, question_choices,
        │   │                              question_numeric, media_assets, question_imports
        │   ├── presets.py         CREATE  rule_presets
        │   └── games.py           CREATE  games, game_players, game_events
        ├── codec/
        │   ├── registry.py        CREATE  wire name ↔ class ↔ current schema_version
        │   ├── upcasters.py       CREATE  the forward chain (empty in production at v1)
        │   └── codec.py           CREATE  encode() / decode()
        ├── repositories/
        │   ├── events.py          CREATE  GameEventStore: append (§4.4) + load_stream
        │   ├── games.py           CREATE  GameRepository: create, list, abandoned lobbies
        │   └── questions.py       CREATE  QuestionBank: FOR SHARE pool selection
        ├── unit_of_work.py        CREATE  UnitOfWork / TransactionContext
        └── migrations/
            ├── env.py             CREATE
            └── versions/
                └── 0001_initial.py CREATE  the whole schema, one revision

backend/tests/
├── conftest.py                            MODIFY  nothing domain-facing; see Task 1
├── test_layering.py                       CREATE  domain purity, mechanized
├── db/
│   ├── conftest.py                        CREATE  engine, migrated schema, truncation
│   ├── test_schema.py                     CREATE  constraints that must exist
│   ├── test_migrations.py                 CREATE  upgrade from empty, alembic check
│   ├── test_wire_names.py                 CREATE  the frozen registry
│   ├── test_codec.py                      CREATE  round-trip, Decimal, unions, upcasters
│   ├── test_golden_corpus.py              CREATE  committed rows decode and fold
│   ├── test_event_store.py                CREATE  append, optimistic check, projection
│   ├── test_game_repository.py            CREATE  genesis commit, listing, abandoned
│   ├── test_question_bank.py              CREATE  selection, insufficiency, FOR SHARE
│   └── golden/
│       ├── README.md                      CREATE  how these were made and when to regenerate
│       ├── expansion_to_battle.json       CREATE
│       ├── surrender_ends_game.json       CREATE
│       └── abort_from_lobby.json          CREATE
└── tools/
    └── generate_golden.py                 CREATE  run by hand; never by the test suite
```

**Why the codec lives in `db/` and not `domain/`:** serialization is a persistence concern and pulls in Pydantic. `domain/` stays importable with zero third-party packages, which is what makes the 277 existing tests run in milliseconds.

**Why one migration for the whole schema** rather than a migration per plan: the foreign-key graph is not separable — `games.host_id → users`, `questions.category_id → categories`, `game_events.game_id → games` — so a partial schema means a permanently half-built test database and a migration chain that cannot be replayed independently. Later plans will add migrations for things the spec did not foresee; that is normal, and different from shipping a deliberately incomplete first revision.

---

## Task ordering

Strictly sequential. Task 2 needs Task 1's database fixture, Task 3 needs Task 2's models, Task 6 needs both Task 3's schema and Task 4's codec, and Task 7 needs Task 6's append. Task 5 needs Task 4. Task 8 needs Task 3 only but is placed last so the two write-path tasks land adjacent.

---

### Task 1: Persistence toolchain, test database, and the layering gate

**Files:**
- Modify: `backend/pyproject.toml`
- Create: `backend/docker-compose.test.yml`, `backend/src/triviador/config.py`, `backend/src/triviador/db/base.py`, `backend/src/triviador/db/engine.py`, `backend/tests/test_layering.py`, `backend/tests/db/conftest.py`, `backend/tests/db/test_connection.py`

**Interfaces:**
- Produces: `Settings`, `get_settings()`, `Base`, `NAMING_CONVENTION`, `create_engine(url)`, `sessionmaker_for(engine)`.
- Consumes: nothing existing. No domain module changes in this task.

This task adds the first third-party runtime dependencies to a project whose `[project] dependencies` is currently `[]`. That is exactly why the layering test belongs here and not later: the moment `sqlalchemy` is installable, an accidental `from sqlalchemy import ...` inside `domain/` becomes possible and would go unnoticed.

- [ ] **Step 1: Write the failing layering test**

Create `backend/tests/test_layering.py`:

```python
"""Spec 1 §4: `domain/` imports nothing from `services/`, `api/`, or `db/`.

Enforced by reading the source, not by convention. The domain's value is that
it is plain Python — the whole ruleset runs in milliseconds with no database,
no event loop, and no third-party package. One stray import ends that quietly.
"""

import ast
from pathlib import Path

DOMAIN = Path(__file__).parent.parent / "src" / "triviador" / "domain"

FORBIDDEN_PREFIXES = (
    "triviador.db",
    "triviador.services",
    "triviador.api",
    "triviador.maps",  # filesystem I/O — `domain/maps/` is the pure half
    "sqlalchemy",
    "asyncpg",
    "alembic",
    "pydantic",
    "fastapi",
)


def _imported_modules(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            names.add(node.module)
    return names


def test_domain_imports_nothing_below_it() -> None:
    violations: list[str] = []
    for path in sorted(DOMAIN.rglob("*.py")):
        for module in sorted(_imported_modules(path)):
            if module.startswith(FORBIDDEN_PREFIXES):
                violations.append(f"{path.relative_to(DOMAIN.parent.parent.parent)}: {module}")
    assert violations == [], "domain/ must stay pure:\n" + "\n".join(violations)


def test_the_gate_can_actually_see_a_violation() -> None:
    """A guard nobody has watched fail is a guard nobody can trust."""
    tree = ast.parse("import sqlalchemy\nfrom triviador.db.models import games\n")
    found = {
        alias.name for node in ast.walk(tree) if isinstance(node, ast.Import)
        for alias in node.names
    }
    assert any(m.startswith(FORBIDDEN_PREFIXES) for m in found)
```

- [ ] **Step 2: Run it and confirm it passes today**

Run: `cd backend && uv run pytest tests/test_layering.py -v --no-cov`
Expected: both PASS. This one starts green on purpose — it is a regression gate for what the rest of the plan is about to make possible, and step 1's second test is what proves it is not vacuously green.

- [ ] **Step 3: Add dependencies**

In `backend/pyproject.toml`:

```toml
dependencies = [
    "sqlalchemy[asyncio]>=2.0.36",
    "asyncpg>=0.30",
    "alembic>=1.14",
    "pydantic>=2.10",
    "pydantic-settings>=2.6",
]

[dependency-groups]
dev = [
    "pytest>=8.3",
    "pytest-asyncio>=0.24",
    "pytest-cov>=6.0",
    "hypothesis>=6.115",
    "mypy>=1.13",
    "ruff>=0.8",
]
```

Add to `[tool.pytest.ini_options]`:

```toml
asyncio_mode = "auto"
markers = ["integration: requires a live PostgreSQL (see docker-compose.test.yml)"]
```

Extend the coverage gate — `codec/` earns the same 100 % branch bar as the reducer, because an unexercised branch there is a class of event that cannot be read back:

```toml
[tool.coverage.report]
include = ["*/domain/game/reducer.py", "*/db/codec/*.py"]
fail_under = 100
```

And exclude generated migration bodies from `mypy`, which cannot usefully type `op.create_table(...)` call sequences:

```toml
[tool.mypy]
exclude = ["src/triviador/db/migrations/versions/"]
```

`env.py` stays inside the strict check — it is hand-written code with real logic.

Run: `cd backend && uv sync`

- [ ] **Step 4: The test database**

Create `backend/docker-compose.test.yml`:

```yaml
# Test-only PostgreSQL. Deliberately on 5433 so it can never collide with a
# real local server on 5432, and `tmpfs` because nothing here should survive
# a run — a test database with leftover state is a test that lies.
services:
  postgres-test:
    image: postgres:17-alpine
    environment:
      POSTGRES_USER: triviador
      POSTGRES_PASSWORD: triviador
      POSTGRES_DB: triviador_test
    ports:
      - "127.0.0.1:5433:5432"
    tmpfs:
      - /var/lib/postgresql/data
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U triviador -d triviador_test"]
      interval: 2s
      timeout: 3s
      retries: 15
```

Create `backend/src/triviador/config.py`:

```python
from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

TEST_DATABASE_URL = "postgresql+asyncpg://triviador:triviador@127.0.0.1:5433/triviador_test"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="TRIVIADOR_", extra="forbid")

    database_url: str = Field(default=TEST_DATABASE_URL)


@lru_cache
def get_settings() -> Settings:
    return Settings()
```

`extra="forbid"` so a typo in an environment variable is a startup failure rather than a silently ignored setting.

- [ ] **Step 5: Base and engine**

Create `backend/src/triviador/db/base.py`:

```python
from sqlalchemy import MetaData
from sqlalchemy.orm import DeclarativeBase

# Explicit names for every constraint. Without this, Alembic emits migrations
# that drop unnamed constraints it cannot address, and a failing check reads as
# `CHECK constraint "ck_1a2b" violated` instead of naming the rule.
NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)
```

Create `backend/src/triviador/db/engine.py`:

```python
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine


def create_engine(url: str, *, echo: bool = False) -> AsyncEngine:
    return create_async_engine(url, echo=echo, pool_pre_ping=True)


def sessionmaker_for(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    # expire_on_commit=False: the runtime reads ORM objects after the
    # transaction context exits (§5.2 resolves origins only then), and a lazy
    # refresh at that point would be I/O on a closed transaction.
    return async_sessionmaker(engine, expire_on_commit=False)


@asynccontextmanager
async def engine_for(url: str) -> AsyncIterator[AsyncEngine]:
    engine = create_engine(url)
    try:
        yield engine
    finally:
        await engine.dispose()
```

- [ ] **Step 6: The database fixture**

Create `backend/tests/db/conftest.py`:

```python
"""Fixtures for the integration suite.

Isolation is TRUNCATE between tests, not an outer transaction rolled back.
Several tests here need two connections to observe each other's committed
work — the optimistic append check and `FOR SHARE` selection are precisely
about cross-transaction visibility — and a wrapping transaction would make
those tests silently meaningless.
"""

import os
from collections.abc import AsyncIterator

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from triviador.config import TEST_DATABASE_URL
from triviador.db.engine import create_engine, sessionmaker_for

pytestmark = pytest.mark.integration

DATABASE_URL = os.environ.get("TRIVIADOR_TEST_DATABASE_URL", TEST_DATABASE_URL)

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
    except Exception as exc:  # noqa: BLE001 - re-raised as a usable message
        await eng.dispose()
        pytest.fail(f"{UNREACHABLE}\n\nunderlying error: {exc!r}")
    yield eng
    await eng.dispose()


@pytest_asyncio.fixture
async def clean_db(engine: AsyncEngine) -> AsyncIterator[None]:
    yield
    async with engine.begin() as conn:
        await conn.execute(
            text(
                "TRUNCATE TABLE game_events, game_players, games, question_imports, "
                "question_choices, question_numeric, questions, categories, "
                "media_assets, rule_presets, sessions, invite_codes, users "
                "RESTART IDENTITY CASCADE"
            )
        )


@pytest_asyncio.fixture
async def sessions(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return sessionmaker_for(engine)
```

The schema itself arrives in Task 3, which is where `TRUNCATE` first has tables to name; until then the fixture is exercised only by the connection smoke test below.

Create `backend/tests/db/test_connection.py`:

```python
import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

pytestmark = pytest.mark.integration


async def test_the_test_database_is_postgres_17_or_newer(engine: AsyncEngine) -> None:
    """SQLite is not a fallback: JSONB, partial unique indexes, FOR SHARE and
    TIMESTAMPTZ semantics are what the rest of this suite asserts."""
    async with engine.connect() as conn:
        version = (await conn.execute(text("SHOW server_version_num"))).scalar_one()
    assert int(version) >= 170000, f"expected PostgreSQL >= 17, got {version}"
```

Also create the empty `backend/tests/db/__init__.py`.

- [ ] **Step 7: Verify both lanes**

```bash
cd backend
docker compose -f docker-compose.test.yml up -d
uv run pytest -m "not integration" -q          # fast lane, no database
uv run pytest tests/db -q --no-cov            # integration lane
uv run ruff check . && uv run ruff format --check . && uv run mypy
```
Expected: fast lane PASSES with the existing 277 tests plus the two layering tests; integration lane PASSES the version check. Then stop the database and confirm the failure is loud:
```bash
docker compose -f docker-compose.test.yml down
uv run pytest tests/db -q --no-cov            # must FAIL with the UNREACHABLE message, not skip
docker compose -f docker-compose.test.yml up -d
```

- [ ] **Step 8: Commit**

```bash
git add backend/pyproject.toml backend/uv.lock backend/docker-compose.test.yml \
        backend/src/triviador/config.py backend/src/triviador/db \
        backend/tests/test_layering.py backend/tests/db
git commit -m "feat(db): persistence toolchain, test database, and the layering gate"
```

---

### Task 2: SQLAlchemy models for the whole schema

**Files:**
- Create: `backend/src/triviador/db/models/{__init__,auth,content,presets,games}.py`
- Create: `backend/tests/db/test_schema.py`

**Interfaces:**
- Produces: `User`, `Session`, `InviteCode`, `Category`, `Question`, `QuestionChoice`, `QuestionNumeric`, `MediaAsset`, `QuestionImport`, `RulePreset`, `Game`, `GamePlayer`, `GameEvent` (ORM row, distinct from the domain `GameEvent` union — import it as `GameEventRow` wherever both are in scope).
- Consumes: `Base` from Task 1.

Spec 1 §7 plus Spec 1B §4.1. Use `Mapped[]` / `mapped_column` throughout — the 2.0 typed style is what makes `mypy --strict` meaningful here.

The name collision is real and worth handling once: `triviador.db.models.games.GameEvent` (a table) versus `triviador.domain.game.events.GameEvent` (a union of 36 dataclasses). Name the ORM class `GameEventRow`. A file that imports both under one name will produce a type error somewhere far from the cause.

- [ ] **Step 1: Write the schema assertions first**

Create `backend/tests/db/test_schema.py` asserting, by querying `information_schema` and `pg_indexes` after the schema exists, that:

```
games                 PK(id) · status NOT NULL · last_seq NOT NULL · rules JSONB NOT NULL
game_players          PK(game_id, user_id) · UNIQUE(game_id, seat)
game_events           PK(game_id, seq) · INDEX(game_id, operation_id)
                      schema_version SMALLINT NOT NULL · payload JSONB NOT NULL
rule_presets          partial unique index on is_default WHERE is_default
                      is_active BOOLEAN NOT NULL DEFAULT true
questions             version NOT NULL · is_active NOT NULL · prompt_hash NOT NULL
question_choices      PK(question_id, idx)
question_numeric      correct_value NUMERIC NOT NULL
media_assets          PK(id)  -- id is the sha256
invite_codes          PK(id) surrogate · UNIQUE(code_hash)          -- A-4
users                 UNIQUE(username)
question_imports      PK(id) · status NOT NULL · report JSONB
every timestamp column is timestamptz
```

Write these as data-driven checks over a table of `(table, constraint kind, columns)` so a missing constraint names itself in the failure. The last one — every timestamp column is `timestamptz` — is a single query over `information_schema.columns` asserting no `timestamp without time zone` exists anywhere.

- [ ] **Step 2: Run it and watch it fail**

Run: `cd backend && uv run pytest tests/db/test_schema.py -q --no-cov`
Expected: FAILS — no tables exist yet. (They arrive in Task 3; this task only defines the models. Re-running at the end of Task 3 is what turns it green, and Task 3's Step 1 depends on that.)

- [ ] **Step 3: Write the models**

`auth.py` — `users`, `sessions`, `invite_codes`. `invite_codes` takes a surrogate `id` primary key with `code_hash` unique (amendment A-4): a secret must not double as its own admin-facing identifier, or every admin URL leaks a live invite.

`content.py` — `categories`, `questions`, `question_choices`, `question_numeric`, `media_assets`, `question_imports`. `questions.version` and `is_active` are both `NOT NULL`; questions are never deleted. `media_assets.id` is the sha256 as `TEXT`, not a generated key — content addressing is what makes the media pipeline idempotent. `question_imports.staged_key` points at the **private** staging bucket (§9.1); add a column comment saying so, because the one-line difference between the two buckets is the difference between staging and publishing answer keys.

`presets.py` — `rule_presets` with `is_active` (soft delete) and:

```python
Index("uq_rule_presets_single_default", "is_default", unique=True,
      postgresql_where=text("is_default"))
```

`games.py` — `games`, `game_players`, `game_events` (as `GameEventRow`):

```python
class GameEventRow(Base):
    __tablename__ = "game_events"

    game_id: Mapped[str] = mapped_column(ForeignKey("games.id"), primary_key=True)
    seq: Mapped[int] = mapped_column(primary_key=True)
    operation_id: Mapped[str] = mapped_column(index=False)
    type: Mapped[str]
    schema_version: Mapped[int] = mapped_column(SmallInteger)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    __table_args__ = (Index("ix_game_events_game_id_operation_id", "game_id", "operation_id"),)
```

`games.status` is a `TEXT` column constrained to `lobby · expansion · battle · finished · aborted` — mirroring `Phase`, which has no `FINAL`; `FinalTiebreak` is a `Turn` variant inside `BATTLE` (§4.2). Use a `CheckConstraint` over literal strings rather than a PostgreSQL `ENUM` type: adding a value to a PG enum inside a transaction has historically been restricted, and the set is small and stable enough that the check constraint costs nothing.

`__init__.py` re-exports every model. This is the single import Alembic's `env.py` will make, and forgetting one model there means autogenerate silently proposes dropping its table.

- [ ] **Step 4: Lint and type-check**

Run: `cd backend && uv run ruff check . && uv run ruff format --check . && uv run mypy`
Expected: PASS. `test_schema.py` still fails — that is Task 3's job.

- [ ] **Step 5: Commit**

```bash
git add backend/src/triviador/db/models backend/tests/db/test_schema.py
git commit -m "feat(db): SQLAlchemy models for the Spec 1 §7 + §4.1 schema"
```

---

### Task 3: Alembic and the initial migration

**Files:**
- Create: `backend/alembic.ini`, `backend/src/triviador/db/migrations/env.py`, `backend/src/triviador/db/migrations/versions/0001_initial.py`
- Create: `backend/tests/db/test_migrations.py`
- Modify: `backend/tests/db/conftest.py` (apply migrations once per session)

**Interfaces:**
- Produces: revision `0001_initial`, and a `migrated_db` session fixture.
- Consumes: `Base.metadata` and every model from Task 2.

**The test schema is created by running migrations, never by `metadata.create_all`.** Otherwise `alembic check` compares models against migrations while the tests exercise a third, differently-built schema, and a migration bug can only be found in production.

- [ ] **Step 1: Write the migration tests**

Create `backend/tests/db/test_migrations.py`:

```python
"""Migrations are the schema's only constructor."""

import pytest

pytestmark = pytest.mark.integration


async def test_upgrade_head_from_empty_database(...) -> None:
    """Drop everything, run `upgrade head`, assert the expected tables exist."""


async def test_alembic_check_is_clean(...) -> None:
    """Models and migrations agree. This is the gate that catches a model field
    added without a migration — the failure that otherwise surfaces as a
    production `UndefinedColumn` long after the change."""


async def test_downgrade_is_not_offered(...) -> None:
    """0001 has no downgrade body beyond dropping everything; assert it raises
    or drops cleanly, and pick one deliberately rather than shipping Alembic's
    autogenerated `pass`."""
```

Implement `test_alembic_check_is_clean` by invoking Alembic's Python API against the live test database, not by shelling out — a subprocess would need its own configuration and would diverge from what CI runs.

- [ ] **Step 2: Run and watch them fail**

Run: `cd backend && uv run pytest tests/db/test_migrations.py -q --no-cov`
Expected: FAIL — no Alembic configuration exists.

- [ ] **Step 3: Initialize Alembic**

```bash
cd backend && uv run alembic init -t async src/triviador/db/migrations
```

Then edit:

- `alembic.ini`: `script_location = src/triviador/db/migrations`; **remove** the `sqlalchemy.url` line entirely — the URL comes from `Settings`, and a URL in a committed ini file is a footgun that eventually points a migration at the wrong database.
- `env.py`: import `Settings` and `Base`, import `triviador.db.models` for its side effect of registering every table, set `target_metadata = Base.metadata`, and enable `compare_type=True` and `compare_server_default=True` so `alembic check` actually notices a type change.

- [ ] **Step 4: Generate and then read the initial migration**

```bash
cd backend && uv run alembic revision --autogenerate -m "initial schema"
```

Rename the generated file to `0001_initial.py` with `revision = "0001_initial"`, `down_revision = None`.

Then **read it line by line against Task 2's models**. Autogenerate reliably misses: the partial unique index on `rule_presets.is_default`, `CheckConstraint` bodies, and column comments. Add whatever is missing by hand. This step is the reason the migration is generated rather than hand-written *and* the reason it is not trusted as generated.

- [ ] **Step 5: Apply migrations in the test fixture**

In `backend/tests/db/conftest.py`, add a session-scoped autouse fixture that, once per session before any test runs, drops the `public` schema and re-runs `upgrade head`:

```python
@pytest_asyncio.fixture(scope="session", autouse=True)
async def migrated_schema(engine: AsyncEngine) -> None:
    async with engine.begin() as conn:
        await conn.execute(text("DROP SCHEMA public CASCADE; CREATE SCHEMA public"))
    await _run_upgrade_head(DATABASE_URL)
```

- [ ] **Step 6: Verify**

```bash
cd backend && uv run pytest tests/db -q --no-cov
```
Expected: `test_schema.py` from Task 2 now PASSES in full, along with all of `test_migrations.py`. If a `test_schema.py` assertion fails here, the migration is wrong — fix the migration, not the assertion.

Then lint and type-check: `uv run ruff check . && uv run ruff format --check . && uv run mypy`

- [ ] **Step 7: Commit**

```bash
git add backend/alembic.ini backend/src/triviador/db/migrations \
        backend/tests/db/test_migrations.py backend/tests/db/conftest.py
git commit -m "feat(db): Alembic and the initial schema migration"
```

---

### Task 4: The event codec — registry, encode, decode, upcasters

**Files:**
- Create: `backend/src/triviador/db/codec/{registry,upcasters,codec}.py`, `backend/src/triviador/db/errors.py`
- Create: `backend/tests/db/test_wire_names.py`, `backend/tests/db/test_codec.py`

**Interfaces:**
- Produces:
  ```python
  def encode(event: GameEvent) -> tuple[str, int, dict[str, Any]]   # wire type, version, payload
  def decode(wire_type: str, schema_version: int, payload: Mapping[str, Any]) -> GameEvent
  WIRE_NAMES: Mapping[type, str]
  CURRENT_VERSION: Mapping[str, int]
  class UnknownEventType(Exception)
  class UnknownSchemaVersion(Exception)
  ```
- Consumes: `triviador.domain.game.events` only. No database, no session — the codec is pure and unit-testable without PostgreSQL, so **these tests are not marked `integration`**.

**Serialization is Pydantic's `TypeAdapter` per event class, not hand-rolled reflection.** Thirty-six dataclasses nesting `GameRules`, `Deadline`, `SubmittedAnswer`, `QuestionPool` and `Decimal` is exactly where a hand-written walker accumulates quiet bugs; Pydantic already handles frozen stdlib dataclasses, `NewType`, `StrEnum`, `tuple[X, ...]`, `Mapping`, and unions. What Pydantic cannot do is notice that a *new* field appeared, so the golden corpus (Task 5) and the exhaustiveness test below are what keep the reflection honest.

- [ ] **Step 1: Write the frozen wire-name registry test**

Create `backend/tests/db/test_wire_names.py` containing the full expected mapping as a literal, and assert three things: every union member is registered, the literal matches the registry exactly, and no two classes share a name.

```python
EXPECTED = {
    "GameCreated": "game.created",
    "PlayerJoined": "game.player_joined",
    "PlayerLeft": "game.player_left",
    "GameStarted": "game.started",
    "BasesAssigned": "game.bases_assigned",
    "QuestionPoolDrawn": "game.question_pool_drawn",
    "MediaWarmupStarted": "game.media_warmup_started",
    "GameFinished": "game.finished",
    "GameAborted": "game.aborted",
    "TerritoryNeutralized": "game.territory_neutralized",
    "QuestionPresented": "question.presented",
    "AnswerSubmitted": "question.answer_submitted",
    "AnswerWindowClosed": "question.window_closed",
    "QuestionResolved": "question.resolved",
    "ExpansionRoundStarted": "expansion.round_started",
    "PicksGranted": "expansion.picks_granted",
    "TerritoryClaimed": "expansion.territory_claimed",
    "ExpansionRoundCompleted": "expansion.round_completed",
    "BattleRoundStarted": "battle.round_started",
    "TurnStarted": "battle.turn_started",
    "TurnSkipped": "battle.turn_skipped",
    "TurnAborted": "battle.turn_aborted",
    "AttackDeclared": "battle.attack_declared",
    "DuelResolved": "battle.duel_resolved",
    "TiebreakStarted": "battle.tiebreak_started",
    "TerritoryCaptured": "battle.territory_captured",
    "NeutralTerritoryCaptured": "battle.neutral_territory_captured",
    "NeutralAttackFailed": "battle.neutral_attack_failed",
    "DefenseHeld": "battle.defense_held",
    "BaseDamaged": "battle.base_damaged",
    "BaseDestroyed": "battle.base_destroyed",
    "BattleRoundCompleted": "battle.round_completed",
    "FinalTiebreakStarted": "battle.final_tiebreak_started",
    "ScoreChanged": "player.score_changed",
    "PlayerEliminated": "player.eliminated",
    "PlayerSurrendered": "player.surrendered",
}
```

Thirty-six entries. Derive the union members with `typing.get_args(GameEvent)` so adding a 37th event to the union without registering it fails here rather than at the first attempt to persist it.

```python
def test_every_event_has_a_wire_name() -> None:
    missing = {t.__name__ for t in get_args(GameEvent)} - set(EXPECTED)
    assert missing == set(), f"unregistered events: {sorted(missing)}"


def test_wire_names_are_frozen() -> None:
    """Changing a value here is a data migration over game_events.type, not an
    edit. If this fails because you renamed a Python class, map the old wire
    name to the new class instead."""
    assert {cls.__name__: name for cls, name in WIRE_NAMES.items()} == EXPECTED


def test_wire_names_are_unique() -> None:
    assert len(set(WIRE_NAMES.values())) == len(WIRE_NAMES)
```

- [ ] **Step 2: Write the codec tests**

Create `backend/tests/db/test_codec.py`:

```python
def test_every_event_type_round_trips() -> None:
    """One constructed instance per union member, encoded and decoded back to
    an equal value. Parametrized over a table of 36 sample events so a new
    event type must be given a sample before it can be merged."""


def test_decimal_survives_as_a_string() -> None:
    """A numeric answer of 0.1 through an IEEE double is a wrong answer.
    Assert the JSON payload holds `"0.1"`, a str, and that the decoded value is
    `Decimal("0.1")` exactly."""


def test_datetime_is_iso_8601_utc_and_aware() -> None:
    """Deadlines are absolute (ADR-001) and compared across a restart."""


def test_answer_value_union_decodes_to_the_right_variant() -> None:
    """`ChoiceAnswer(idx=0)` and `NumericAnswer(Decimal(0))` are structurally
    distinct but both are bare dataclasses in an undiscriminated union.
    Assert each decodes to its own type, not merely to something equal."""


def test_payload_is_json_serializable() -> None:
    """`json.dumps(payload)` must succeed with no default= hook: this is what
    JSONB will receive, and a stray Decimal or datetime object would only fail
    at insert time."""


def test_unknown_wire_type_raises() -> None: ...
def test_unknown_schema_version_raises(): ...
```

Plus a Hypothesis round-trip property over generated events if the strategies are cheap to build; if they are not, say so and leave the parametrized table as the coverage. Do not build elaborate strategies for their own sake — the golden corpus is the stronger guard.

- [ ] **Step 3: Run and watch them fail**

Run: `cd backend && uv run pytest tests/db/test_wire_names.py tests/db/test_codec.py -q --no-cov`
Expected: collection error — no `triviador.db.codec` module.

- [ ] **Step 4: Implement the registry**

`registry.py` holds `WIRE_NAMES: Mapping[type[Any], str]` and its inverse, plus `CURRENT_VERSION: Mapping[str, int]` defaulting every wire type to `1`. Both are module-level literals, not derived from class names — §4.3's whole point is that the wire name is decoupled from the Python identifier so a refactor cannot rewrite history.

- [ ] **Step 5: Implement encode/decode**

```python
def encode(event: GameEvent) -> tuple[str, int, dict[str, Any]]:
    wire_type = WIRE_NAMES[type(event)]
    adapter = _adapter_for(type(event))
    return wire_type, CURRENT_VERSION[wire_type], adapter.dump_python(event, mode="json")


def decode(wire_type: str, schema_version: int, payload: Mapping[str, Any]) -> GameEvent:
    cls = CLASSES_BY_WIRE_NAME.get(wire_type)
    if cls is None:
        raise UnknownEventType(wire_type)
    upcast = upcast_chain(wire_type, schema_version)   # raises UnknownSchemaVersion
    return _adapter_for(cls).validate_python(upcast(dict(payload)))
```

Cache `TypeAdapter` instances per class (`functools.lru_cache`) — building one per event during a 300-event replay is pure waste.

If `dump_python(mode="json")` does not produce a string for `Decimal`, add an explicit annotation or serializer rather than accepting a float. Step 2's `test_decimal_survives_as_a_string` decides this, in that direction only.

- [ ] **Step 6: Implement the upcaster chain — and test it against a real chain**

`upcasters.py`:

```python
Upcaster = Callable[[dict[str, Any]], dict[str, Any]]

# (wire_type, from_version) -> transform producing from_version + 1.
# Empty at v1: nothing has been renamed, retyped, or removed yet.
UPCASTERS: Mapping[tuple[str, int], Upcaster] = {}


def upcast_chain(wire_type: str, from_version: int) -> Upcaster:
    """Compose forward until the payload matches the current version."""
```

The mechanism has no production users at v1, and a test that runs an empty chain proves nothing. Test it against a **test-local registry** with a synthetic three-version event: `v1 → v2` renames a field, `v2 → v3` adds one with a default. Assert a v1 payload reaches the v3 shape, that a version above current raises, and that an unregistered intermediate step raises rather than silently skipping. Do not invent a fake version bump on a real event to make the test look production-shaped — that would put a lie in the registry.

- [ ] **Step 7: Verify, including the coverage gate**

```bash
cd backend && uv run pytest tests/db/test_codec.py tests/db/test_wire_names.py -q --no-cov
uv run pytest -m "not integration" -q          # the codec tests run in the fast lane
uv run ruff check . && uv run ruff format --check . && uv run mypy
```
Expected: all PASS, and the coverage report shows `db/codec/*` at 100 % branch (Task 1 added it to `include`). If a branch is unreachable, delete it rather than adding a contrived test.

- [ ] **Step 8: Commit**

```bash
git add backend/src/triviador/db/codec backend/src/triviador/db/errors.py \
        backend/tests/db/test_codec.py backend/tests/db/test_wire_names.py
git commit -m "feat(db): event codec with a frozen wire-name registry and upcaster chain"
```

---

### Task 5: The golden corpus

**Files:**
- Create: `backend/tests/tools/generate_golden.py`, `backend/tests/db/golden/README.md`, three `golden/*.json` files
- Create: `backend/tests/db/test_golden_corpus.py`

**Interfaces:**
- Produces: committed corpus files and the test that reads them.
- Consumes: `decode` from Task 4, `create_initial_state` and `fold` from the domain.

§4.3: "The guard is a golden corpus." Its value depends entirely on one property — **the test must never re-encode**. It reads committed JSON, decodes, folds, and compares to a committed expected summary. A test that encodes and then decodes its own output asserts that the codec agrees with itself, which is true of every broken codec too.

- [ ] **Step 1: Write the generator**

`backend/tests/tools/generate_golden.py` plays three scripted trajectories through `decide`/`fold` with a fixed clock and a fixed question pool, encodes each event, and writes:

```json
{
  "name": "expansion_to_battle",
  "generated_from": "<git sha>",
  "rows": [
    {"seq": 1, "type": "game.created", "schema_version": 1, "payload": {...}},
    ...
  ],
  "expected": {
    "seq": 47,
    "phase": "battle",
    "round_no": 2,
    "winner_id": null,
    "scores": {"p1": 1200, "p2": 800, "p3": 400},
    "territories": {"r1": {"owner_id": "p1", "kind": "base", "base_hp": 3}, ...},
    "eliminated": [],
    "next_deadline_id": 12
  }
}
```

The three trajectories:
1. `expansion_to_battle` — creation, three joins, start, warmup, a full expansion phase into battle round 2. The broad one.
2. `surrender_ends_game` — the Plan 2 fix: a surrender leaving one active player finishes the game. Its expected `winner_id` is not null.
3. `abort_from_lobby` — genesis, one join, a system-authorized abort. Short, and the only corpus entry covering the `actor_id=None` path.

Run it **by hand**, once. It is under `tests/tools/`, not `tests/`, and its filename does not start with `test_` so pytest never collects it.

- [ ] **Step 2: Write the corpus test**

```python
"""Committed event rows must keep decoding and folding to the same state.

This is the one test that can catch a semantic change to the reducer — a JSON
shape check cannot. Read only: nothing here calls `encode`."""

CORPUS = sorted((Path(__file__).parent / "golden").glob("*.json"))


@pytest.mark.parametrize("path", CORPUS, ids=lambda p: p.stem)
def test_corpus_decodes_and_folds_to_the_expected_state(path: Path) -> None:
    doc = json.loads(path.read_text(encoding="utf-8"))
    events = [decode(r["type"], r["schema_version"], r["payload"]) for r in doc["rows"]]
    state = create_initial_state(events[0], GameId(doc["game_id"]), MAP)
    state = fold(state, events[1:])
    assert summarize(state) == doc["expected"]


def test_the_corpus_is_not_empty() -> None:
    """A glob that silently matches nothing is a test suite that passes by
    finding no work to do."""
    assert len(CORPUS) == 3
```

`summarize(state)` lives in the test module and projects phase, round, scores, territory ownership/level, eliminations, winner, and `next_deadline_id`. Deliberately **not** a serialized `GameState`: a whole-object snapshot breaks on every field addition and trains people to regenerate the corpus without reading the diff, which destroys the guard. The summary covers the observable state and is stable under additive change.

- [ ] **Step 3: Write the README**

`golden/README.md` states: what each trajectory covers, that regeneration is `uv run python tests/tools/generate_golden.py`, and — the part that matters — **that a diff in these files during an unrelated change is a finding, not a chore.** Regenerate only when a domain change is intended to alter history, and review the diff as carefully as the code change that caused it.

- [ ] **Step 4: Verify the guard actually bites**

Temporarily change something semantic in the reducer — e.g. make expansion claims award a different score — and run the corpus test. It must fail. Revert. Then temporarily rename a field in an event dataclass without an upcaster: the corpus must fail to decode. Revert. Record both observations in the commit message; a golden corpus nobody has watched fail is decoration.

- [ ] **Step 5: Full verification**

```bash
cd backend && uv run pytest -m "not integration" -q
uv run ruff check . && uv run ruff format --check . && uv run mypy
```

- [ ] **Step 6: Commit**

```bash
git add backend/tests/tools backend/tests/db/golden backend/tests/db/test_golden_corpus.py
git commit -m "test(db): golden corpus for codec and reducer semantics"
```

---

### Task 6: `UnitOfWork` and `GameEventStore` — the optimistic append and the read-model projection

**Files:**
- Create: `backend/src/triviador/db/unit_of_work.py`, `backend/src/triviador/db/repositories/events.py`
- Create: `backend/tests/db/test_event_store.py`

**Interfaces:**
- Produces:
  ```python
  class UnitOfWork:
      def begin(self) -> AsyncContextManager[TransactionContext]: ...

  class TransactionContext:
      session: AsyncSession
      async def append(self, game_id: GameId, *, expected_last_seq: int,
                       events: Sequence[GameEvent], operation_id: str) -> None: ...
      async def load_stream(self, game_id: GameId) -> tuple[GameEvent, ...]: ...
      async def events_for_operation(self, game_id: GameId,
                                     operation_id: str) -> tuple[int, ...]: ...

  class ConcurrentModification(Exception): ...
  ```
- Consumes: Task 3's schema, Task 4's `encode`/`decode`.

This is the heart of the plan. Two things must be true and both are testable:

**The optimistic check is one statement (§4.4).** `UPDATE games SET last_seq = :new WHERE id = :gid AND last_seq = :expected` takes the row lock *and* performs the check; `rowcount == 0` raises `ConcurrentModification`, which the runtime will quarantine on and **never retry** — a mismatch means someone else advanced this game, and retrying would append events decided against a stale state.

**The read model is projected in the same transaction (§4.2).** `append` also applies:

```
games.last_seq         always
games.status           from Phase-bearing events (GameStarted → expansion, …)
games.started_at       GameStarted
games.finished_at      GameFinished / GameAborted
games.winner_id        GameFinished
game_players           INSERT on PlayerJoined
game_players           DELETE on PlayerLeft     ← without this, Plan 2's seat fix still
                                                  collides with UNIQUE(game_id, seat)
game_players.final_score  GameFinished
```

That `PlayerLeft` delete is not a detail. Plan 2 changed `_decide_join` to allocate the lowest unused seat precisely so a departure frees its number; if the projection leaves the old `game_players` row behind, the next join re-inserts that seat and violates the unique constraint. The test below is the one that would have caught shipping half the fix.

- [ ] **Step 1: Write the tests**

Create `backend/tests/db/test_event_store.py`:

```python
async def test_append_writes_events_and_advances_last_seq(...): ...
async def test_appended_events_read_back_identical(...):
    """Round-trip through PostgreSQL, not just through the codec: JSONB
    normalizes key order and rejects some values the codec might emit."""

async def test_stale_expected_last_seq_raises_concurrent_modification(...):
    """Two transactions, both computing from seq=5. The second must raise —
    and must not have written anything."""

async def test_concurrent_modification_leaves_no_partial_events(...):
    """The rollback is what makes the check meaningful."""

async def test_status_started_at_finished_at_winner_are_projected(...): ...
async def test_player_joined_inserts_a_game_player_row(...): ...

async def test_player_left_deletes_the_row_so_the_seat_can_be_reused(...):
    """p2 leaves seat 1; p4 joins and takes seat 1. Without the DELETE this
    violates UNIQUE(game_id, seat) — the database half of Plan 2's seat fix."""

async def test_final_scores_are_projected_on_game_finished(...): ...

async def test_events_for_operation_returns_the_exact_seq_range(...):
    """§5.5 reconciliation compares an exact expected range. Assert the seqs,
    not merely the count."""

async def test_append_is_rejected_when_events_is_empty(...):
    """§5.2 resolves a no-op before reaching append; an empty append that
    silently advanced last_seq would corrupt the stream."""
```

- [ ] **Step 2: Run and watch them fail**

Run: `cd backend && uv run pytest tests/db/test_event_store.py -q --no-cov`
Expected: collection error — no `unit_of_work` module.

- [ ] **Step 3: Implement `UnitOfWork`**

One transaction per `begin()`. It exposes the session so `StartGame`'s materialiser (Plan 4) can run its `FOR SHARE` selection inside the same transaction that appends (§5.3). No autocommit, no nested `begin`, and no `commit()` method on `TransactionContext` — the context manager owns the boundary, because §5.2 requires that origins resolve only after it exits.

- [ ] **Step 4: Implement `append`**

Order matters and is not arbitrary: the `UPDATE` runs first, taking the row lock before any insert, so two concurrent appends serialize on the `games` row rather than racing on the `game_events` primary key.

```python
async def append(self, game_id, *, expected_last_seq, events, operation_id) -> None:
    if not events:
        raise ValueError("append requires at least one event; a no-op resolves earlier")

    new_seq = expected_last_seq + len(events)
    result = await self.session.execute(
        update(Game)
        .where(Game.id == game_id, Game.last_seq == expected_last_seq)
        .values(last_seq=new_seq)
    )
    if result.rowcount == 0:
        raise ConcurrentModification(game_id, expected_last_seq)

    for offset, event in enumerate(events, start=expected_last_seq + 1):
        wire_type, version, payload = encode(event)
        self.session.add(GameEventRow(game_id=game_id, seq=offset, operation_id=operation_id,
                                      type=wire_type, schema_version=version, payload=payload))
    await self._project(game_id, events)
```

`_project` walks the events and applies the read-model table above. Structure it as a `match` over event types so a new event type that should affect `games` or `game_players` is a visible omission rather than an invisible one.

- [ ] **Step 5: Implement `load_stream` and `events_for_operation`**

`load_stream` orders by `seq` and decodes every row. `events_for_operation` returns the `seq` values for one `operation_id`, ordered — Plan 4's reconciliation compares an exact expected range, so returning a count would not be enough.

- [ ] **Step 6: Verify**

```bash
cd backend && uv run pytest tests/db -q --no-cov
uv run ruff check . && uv run ruff format --check . && uv run mypy
```
Expected: all PASS.

- [ ] **Step 7: Commit**

```bash
git add backend/src/triviador/db/unit_of_work.py backend/src/triviador/db/repositories/events.py \
        backend/tests/db/test_event_store.py
git commit -m "feat(db): optimistic event append with the read model in one transaction"
```

---

### Task 7: `GameRepository` — genesis, listing, abandoned lobbies

**Files:**
- Create: `backend/src/triviador/db/repositories/games.py`
- Create: `backend/tests/db/test_game_repository.py`

**Interfaces:**
- Produces:
  ```python
  class GameRepository:
      async def create(self, *, game_id, map_id, rules, host_id, map_sha256,
                       preset_id, operation_id) -> None:       # §6.2 tx1
      async def get_summary(self, game_id) -> GameSummary | None
      async def list_joinable(self) -> tuple[GameSummary, ...]
      async def find_abandoned_lobbies(self, *, older_than: datetime) -> tuple[GameId, ...]
      async def find_unfinished(self) -> tuple[GameId, ...]
  ```
- Consumes: Task 6's transaction context and codec.

`create` implements §6.2's `tx1` exactly: `INSERT games (status='lobby', last_seq=1)` and `INSERT game_events (seq=1, 'game.created')` in one transaction, and **nothing else**. The host does not join here — `PlayerJoined` goes through the runtime queue (§6.2), because putting seat allocation on a second mutation path is what §8.2 forbids and what Plan 2 just finished repairing.

`find_abandoned_lobbies` is what Plan 4's reaper calls, and it must find lobbies **that no runtime has loaded** — the failure mode is a crash between `tx1` and the host's join, leaving a player-less lobby that only the database knows about. Query `games` directly, joined against `game_players`, not any in-memory registry.

`find_unfinished` returns games in `expansion` or `battle` for startup recovery. Per the §1.1 clarification, there is no `final` status to query — `FinalTiebreak` is a `Turn` inside `BATTLE`.

- [ ] **Step 1: Write the tests**

```python
async def test_create_writes_exactly_one_event_and_one_game_row(...):
    """seq=1, type='game.created', status='lobby', last_seq=1 — and zero
    game_players rows. The host joins through the runtime."""

async def test_created_game_decodes_to_a_genesis_state(...):
    """load_stream → create_initial_state produces a GameState at seq=1 with
    the map_sha256 the caller supplied."""

async def test_list_joinable_hides_a_player_less_lobby(...):
    """§6.2's crash window: GET /api/games must not advertise a lobby nobody
    can be in."""

async def test_find_abandoned_lobbies_finds_a_lobby_only_the_database_knows(...):
    """The reaper's target: created, never joined, older than the cutoff."""

async def test_find_abandoned_lobbies_ignores_a_populated_lobby(...): ...
async def test_find_abandoned_lobbies_ignores_a_recent_one(...): ...
async def test_find_unfinished_returns_expansion_and_battle_only(...):
    """Not lobby, not finished, not aborted."""
```

- [ ] **Step 2: Run, fail, implement, verify**

Run: `cd backend && uv run pytest tests/db/test_game_repository.py -q --no-cov` — expect a collection error, then implement, then:
```bash
uv run pytest tests/db -q --no-cov
uv run ruff check . && uv run ruff format --check . && uv run mypy
```

- [ ] **Step 3: Commit**

```bash
git add backend/src/triviador/db/repositories/games.py backend/tests/db/test_game_repository.py
git commit -m "feat(db): game repository with genesis creation and abandoned-lobby lookup"
```

---

### Task 8: `QuestionBank` — pool selection under `FOR SHARE`

**Files:**
- Create: `backend/src/triviador/db/repositories/questions.py`
- Create: `backend/tests/db/test_question_bank.py`

**Interfaces:**
- Produces:
  ```python
  class QuestionBank:
      async def select_pool(self, budget: QuestionBudget) -> QuestionPool: ...
  class InsufficientQuestions(Exception):
      kind: QuestionKind
      required: int
      available: int
  ```
- Consumes: Task 3's `questions` / `question_choices` / `question_numeric` tables, the domain's `QuestionSnapshot` / `QuestionPool` / `QuestionBudget`.

§5.3's selection, run inside the appending transaction:

```sql
SELECT q.* FROM questions q
 WHERE q.is_active AND q.kind = :kind
 ORDER BY random() LIMIT :n
   FOR SHARE;
```

Fewer than `:n` rows raises `InsufficientQuestions`, which Plan 4 maps to `RejectedCommand(QUESTION_POOL_INSUFFICIENT)`; the transaction rolls back and the game stays in `LOBBY`.

**Locking the parent `questions` row is sufficient only because every semantic edit bumps `questions.version`,** which touches that row (§5.3). That makes the version-bump rule a locking invariant rather than bookkeeping. Plan 7 owns the admin side of it; this task adds the note in the docstring where it will be read, and Plan 7 adds the test that a choice edit bumps `version`.

`select_pool` returns fully materialized `QuestionSnapshot` values — prompt, category, difficulty, choices, numeric answer, unit, media asset — because once the pool is drawn the game never reads the bank again. A lazily-loaded relationship here would be a game reading admin-editable rows mid-flight.

- [ ] **Step 1: Write the tests**

```python
async def test_select_pool_returns_the_requested_counts(...): ...
async def test_snapshots_are_fully_materialized(...):
    """Every field populated, choices included, no lazy load: assert by
    detaching the session and reading the snapshot afterwards."""

async def test_inactive_questions_are_never_selected(...): ...

async def test_insufficient_questions_raises_with_the_shortfall(...):
    """The exception names kind, required, and available — the operator needs
    to know which bank to fill, and Plan 4 puts this in the reject reason."""

async def test_selection_holds_a_share_lock_for_the_transaction(...):
    """Two connections: the second attempts `SELECT ... FOR UPDATE` on a
    selected row and must block until the first commits. Assert with a short
    statement timeout so a broken lock fails fast rather than hanging the
    suite."""

async def test_a_share_lock_does_not_block_another_reader(...):
    """FOR SHARE, not FOR UPDATE: two concurrent StartGame commands on
    different games must both proceed."""
```

The lock tests need a timeout, not a `sleep`. Use `SET LOCAL lock_timeout` and assert the expected outcome; a test that waits on wall-clock time is the thing §12.2 forbids.

- [ ] **Step 2: Run, fail, implement, verify**

```bash
cd backend && uv run pytest tests/db/test_question_bank.py -q --no-cov
# implement, then:
uv run pytest -q                                # full fast lane + coverage gates
uv run pytest tests/db -q --no-cov              # full integration lane
uv run ruff check . && uv run ruff format --check . && uv run mypy
```

- [ ] **Step 3: Commit**

```bash
git add backend/src/triviador/db/repositories/questions.py backend/tests/db/test_question_bank.py
git commit -m "feat(db): question bank pool selection under a share lock"
```

---

## Done criteria

- [ ] `uv run pytest -m "not integration" -q` passes, including the existing 277 domain tests unchanged.
- [ ] `uv run pytest tests/db -q --no-cov` passes against a live PostgreSQL 17, and **fails loudly** — never skips — when the database is down.
- [ ] `db/codec/*` is at 100 % branch coverage; `reducer.py` still is.
- [ ] `alembic check` is clean and `upgrade head` succeeds from an empty database.
- [ ] The layering test proves `domain/` imports no persistence code, and its own negative case is exercised.
- [ ] All 36 events in the `GameEvent` union are registered, and the frozen wire-name list matches.
- [ ] The golden corpus has been *watched to fail* on both a semantic reducer change and an unmigrated field rename.
- [ ] `ConcurrentModification` has been observed leaving zero rows behind.
- [ ] A `PlayerLeft` frees its seat in `game_players`, and a subsequent join reuses it without violating `UNIQUE(game_id, seat)`.
- [ ] `ruff check`, `ruff format --check`, and `mypy --strict` are clean.

---

## What this plan does not do

- **No runtime.** `GameManager`, `GameRuntime`, the consumer loop, deadlines, quarantine, the watchdog, the reaper, and recovery are Plan 4. This plan builds only what they call.
- **No `map_sha256` verification.** Plan 2 produces the digest and this plan stores it; comparing it against the map on disk and refusing to load on mismatch belongs to recovery, in Plan 4. Until then that invariant lives only in prose — this is a known, deliberate gap carried forward from Plan 2.
- **No recovery orchestration.** `load_stream` returns decoded events and the golden corpus proves they fold correctly, but nothing yet rebuilds a live game from them.
- **No auth logic.** `users`, `sessions`, and `invite_codes` get tables and models; password hashing, session issuance, and revocation are Plan 5.
- **No admin write paths.** `questions`, `media_assets`, `question_imports`, and `rule_presets` get tables; the import pipeline, media processing, and the `questions.version` bump enforcement are Plan 7.
- **No snapshots.** Spec 1 §7: a game is a few hundred events and `fold` on restart is instant. Revisit only if that stops being true.
- **No production compose service.** `docker-compose.test.yml` is for the test suite; the deployed stack is Plan 8.
