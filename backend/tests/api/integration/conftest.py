"""The real app, over real PostgreSQL, driven by Starlette's `TestClient`.

**Why the tests here are synchronous.** `TestClient` runs the ASGI app on
its own event loop in its own thread — that is what lets it offer a
*blocking* `websocket_connect`, which is the only ergonomic way to script
a socket conversation. An `async def` test would then be nesting two loops,
and `tests/db/conftest.py`'s session-scoped asyncpg engine is bound to the
outer one. So this directory does not use those fixtures at all: it owns a
throwaway engine per helper call, via `asyncio.run`, on whichever thread
the caller happens to be. Every connection is opened and closed inside one
`asyncio.run`, so nothing is ever shared across loops.

**Why real time passes here.** §12.2 forbids waiting on wall-clock time for
*game logic* — and this suite does not: every window is closed by a player
answering, never by a timeout. The one unavoidable wait is `MediaWarmup`,
which is a fixed duration by construction (ADR-003 forbids a rule that
depends on client readiness), so the preset sets it to the 1 s floor. Spec
1 §12.4's Playwright smoke has exactly the same property.
"""

import asyncio
import os
import warnings
from collections.abc import Callable, Coroutine, Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from pydantic import SecretStr
from sqlalchemy import text

# `fastapi.testclient` re-exports Starlette's `TestClient`, which warns
# once at import time that httpx (rather than httpx2) is in use. That
# warning fires from this, the first import of `starlette.testclient`
# anywhere in this suite — collection time, before any per-test
# `filterwarnings` marker is active — so it is suppressed narrowly, right
# here, rather than through a project-wide ini setting or a blanket
# ignore. Nothing else in this repository imports `fastapi.testclient`
# (grep confirms it), so this is the one place it can fire.
with warnings.catch_warnings():
    warnings.filterwarnings(
        "ignore",
        message=r"Using `httpx` with `starlette\.testclient` is deprecated",
    )
    from fastapi.testclient import TestClient

from tests.runtime.integration.conftest import write_grid_map
from tests.storage.conftest import ENDPOINT, KEY_ID, KEY_SECRET
from triviador.config import Settings
from triviador.domain.game.rules import GameRules

pytestmark = pytest.mark.integration

DATABASE_URL = os.environ.get(
    "TRIVIADOR_TEST_DATABASE_URL",
    "postgresql+asyncpg://triviador:triviador@127.0.0.1:5433/triviador_test",
)

HERE = Path(__file__).parent


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    """Every module under this directory must declare `pytestmark =
    pytest.mark.integration` — the same guard `tests/db/conftest.py` and
    `tests/runtime/integration/conftest.py` apply to their own directories.
    A `pytest_collection_modifyitems` hook is registered for the whole
    session once loaded, not scoped to this directory, so `items` here is
    everything collected under `testpaths`; both filter to items whose file
    lives under `HERE`.

    Unlike those two siblings, there is no loop-scope half here: every test
    in this directory is deliberately synchronous (see the module docstring
    above), so there is no session-scoped async loop to opt into.
    """
    ours = [item for item in items if item.path.is_relative_to(HERE)]
    unmarked = sorted({i.nodeid.split("::")[0] for i in ours if "integration" not in i.keywords})
    if unmarked:
        raise pytest.UsageError(
            "tests/api/integration modules must declare `pytestmark = "
            "pytest.mark.integration`; missing in: " + ", ".join(unmarked)
        )


# 2 players, one expansion round, one battle round, every window at its
# floor. `required_question_budget` for this is 4 numeric + 2 MC.
FAST_RULES = GameRules(
    player_count=2,
    expansion_rounds=1,
    battle_rounds=1,
    base_hp=1,
    answer_timeout_ms=3_000,
    pick_timeout_ms=3_000,
    warmup_ms=1_000,
    claims_by_rank=(2, 1),
    pts_base=1000,
    pts_territory=200,
    pts_conquered=400,
    pts_defense=100,
)


def run[T](coro: Coroutine[object, object, T]) -> T:
    """Every database helper here opens its own engine inside its own
    `asyncio.run`, so no connection outlives the loop it was created on."""
    return asyncio.run(coro)


@pytest.fixture(scope="session")
def migrated() -> None:
    config = Config(str(Path(__file__).resolve().parents[3] / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", DATABASE_URL)
    command.upgrade(config, "head")


@pytest.fixture
def seeded(migrated: None, tmp_path: Path) -> Path:
    """Truncate, then seed: two users, an invite for each, a question bank
    covering `FAST_RULES`, and a `fast` preset. Returns the maps root."""
    from dataclasses import asdict

    from sqlalchemy import insert

    from tests.db.conftest import (
        _seed_category,
        _seed_mc_question,
        _seed_numeric_question,
        _seed_user,
    )
    from triviador.db.engine import engine_for, sessionmaker_for
    from triviador.db.models.presets import RulePreset
    from triviador.db.security import Argon2Hasher
    from triviador.db.seed import DEFAULT_PRESET_RULES

    async def seed() -> None:
        async with engine_for(DATABASE_URL) as engine:
            sessions = sessionmaker_for(engine)
            async with sessions() as db, db.begin():
                # Order matters: `game_events` and `game_players` reference
                # `games`, which references `users` and `rule_presets`.
                for table in (
                    "game_events",
                    "game_players",
                    "games",
                    "sessions",
                    "invite_codes",
                    "question_choices",
                    "question_numeric",
                    "question_imports",
                    "media_assets",
                    "questions",
                    "categories",
                    "users",
                    "rule_presets",
                ):
                    await db.execute(text(f"TRUNCATE {table} CASCADE"))
                # Re-seed migration 0002's row from the same frozen literal
                # it used. Truncating and restoring beats excluding the
                # table: an excluded table preserves *mutations* between
                # tests, and this suite creates games that read the default.
                await db.execute(
                    insert(RulePreset).values(
                        id="default",
                        name="Default",
                        is_default=True,
                        rules=dict(DEFAULT_PRESET_RULES),
                        version=1,
                        is_active=True,
                    )
                )
            hasher = Argon2Hasher()
            for name in ("alice", "bob"):
                await _seed_user(sessions, name)
                async with sessions() as db, db.begin():
                    await db.execute(
                        text(
                            "UPDATE users SET username = :u, password_hash = :p, "
                            "display_name = :d, role = 'player' WHERE id = :u"
                        ),
                        {"u": name, "p": hasher.hash("correct horse"), "d": name.title()},
                    )
            await _seed_category(sessions)
            for i in range(4):
                await _seed_numeric_question(sessions, f"num-{i}")
            for i in range(2):
                await _seed_mc_question(sessions, f"mc-{i}")
            async with sessions() as db, db.begin():
                await db.execute(
                    insert(RulePreset).values(
                        id="fast",
                        name="Fast",
                        is_default=False,
                        rules=asdict(FAST_RULES),
                        version=1,
                        is_active=True,
                    )
                )

    run(seed())
    write_grid_map(tmp_path / "grid")
    return tmp_path


@pytest.fixture
def client(seeded: Path) -> Iterator[TestClient]:
    from triviador.api.app import build_app

    settings = Settings(
        database_url=DATABASE_URL,
        allowed_origins=("http://testserver",),
        allowed_hosts=("testserver",),
        cookie_secure=False,
        maps_root=seeded,
        log_format="console",
        # Task 2 made these mandatory at startup. The suite does not touch
        # object storage yet; it has to be *configured* to boot, which is
        # the whole point of the assertion.
        s3_endpoint_url=ENDPOINT,
        s3_region="garage",
        s3_access_key_id=KEY_ID,
        s3_secret_access_key=SecretStr(KEY_SECRET),
    )
    with TestClient(build_app(settings), base_url="http://testserver") as client:
        client.headers["Origin"] = "http://testserver"
        yield client


from triviador.api.app import build_dependencies, create_app  # noqa: E402
from triviador.cli import admin_create  # noqa: E402
from triviador.db.engine import engine_for, sessionmaker_for  # noqa: E402
from triviador.db.repositories.auth import UserRepository  # noqa: E402
from triviador.db.repositories.media import MediaAssetRepository  # noqa: E402
from triviador.db.security import Argon2Hasher  # noqa: E402
from triviador.media.gc import GcReport, MediaCollector  # noqa: E402
from triviador.services.storage import ObjectHead  # noqa: E402
from triviador.storage.s3 import S3MediaStore  # noqa: E402


def admin_settings(maps_root: Path) -> Settings:
    """The real `Settings`, pointed at both test containers.

    Buckets are shared with `tests/storage/` on purpose: every key in
    them is either a content hash or a per-import uuid prefix, so two
    suites cannot collide, and a second pair of buckets would be a second
    thing `garage-init.sh` has to keep in step.

    The brief's literal version hardcoded `maps_root=HERE / "maps"` — a
    fixed directory *inside the test source tree*, rather than the
    `client` fixture's own `maps_root=seeded` (a pytest `tmp_path`).
    That writes real `write_grid_map` output next to this file on every
    run, and `git add backend/tests` (Step 4) would pick the files up as
    untracked. Taking `maps_root` as a parameter and passing `seeded`
    keeps this fixture on the same, already-proven pattern.
    """
    return Settings(
        database_url=DATABASE_URL,
        allowed_origins=("http://testserver",),
        allowed_hosts=("testserver",),
        maps_root=maps_root,
        s3_endpoint_url=ENDPOINT,
        s3_region="garage",
        s3_access_key_id=KEY_ID,
        s3_secret_access_key=SecretStr(KEY_SECRET),
    )


@pytest.fixture
def admin_session(seeded: Path) -> Iterator[tuple[TestClient, Settings]]:
    """A migrated database, one bootstrapped admin, and a signed-in client.

    `admin_create` is called as a function rather than through a
    subprocess: it is the same code path `uv run triviador admin-create`
    takes, and a subprocess here would need its own environment and its
    own database URL to get wrong.
    """
    # `seeded` already wrote the grid map into `tmp_path / "grid"` before
    # returning it — a second `write_grid_map` call here would be redundant
    # (and, worse, land in the source tree; see `admin_settings`'s docstring).
    settings = admin_settings(seeded)

    async def _bootstrap() -> None:
        async with engine_for(settings.database_url) as engine:
            sessions = sessionmaker_for(engine)
            await admin_create(
                users=UserRepository(sessions),
                hasher=Argon2Hasher(),
                username="root",
                password="correct horse battery",
                display_name="Root",
                force=False,
            )

    run(_bootstrap())
    built = build_dependencies(settings)
    with TestClient(create_app(built.deps)) as client:
        response = client.post(
            "/api/auth/login",
            json={"username": "root", "password": "correct horse battery"},
            headers={"Origin": "http://testserver"},
        )
        assert response.status_code == 200
        client.headers["Origin"] = "http://testserver"
        yield client, settings
    run(built.engine.dispose())


class _SyncMediaStore:
    """`S3MediaStore` with a blocking face.

    This directory's tests are synchronous (see the module docstring), so
    every call owns its own loop through `asyncio.run` — the same
    convention the database helpers here already use.
    """

    def __init__(self, settings: Settings) -> None:
        self._store = S3MediaStore(
            endpoint_url=settings.s3_endpoint_url,
            region=settings.s3_region,
            access_key_id=settings.s3_access_key_id,
            secret_access_key=settings.s3_secret_access_key.get_secret_value(),
            bucket=settings.media_bucket,
        )

    def head_sync(self, key: str) -> ObjectHead | None:
        # The brief types this `object | None`, which is `S3MediaStore.head`'s
        # actual return type erased — every caller in `test_admin_session.py`
        # then reads `.content_type`/`.cache_control` off it, which `mypy
        # --strict` rejects on a bare `object`. `ObjectHead` is what `head`
        # really returns; naming it here is what makes those reads type-check.
        return asyncio.run(self._store.head(key))

    @property
    def store(self) -> S3MediaStore:
        return self._store


@pytest.fixture
def media_store(admin_session: tuple[TestClient, Settings]) -> _SyncMediaStore:
    return _SyncMediaStore(admin_session[1])


@pytest.fixture
def run_media_gc(
    admin_session: tuple[TestClient, Settings], media_store: _SyncMediaStore
) -> Callable[..., GcReport]:
    settings = admin_session[1]

    def run(*, dry_run: bool = False) -> GcReport:
        async def _go() -> GcReport:
            async with engine_for(settings.database_url) as engine:
                collector = MediaCollector(
                    assets=MediaAssetRepository(sessionmaker_for(engine)),
                    store=media_store.store,
                    # Zero grace: the fixture's uploads are seconds old, and
                    # the production default (60 minutes) would make every
                    # orphan assertion in this suite vacuously pass.
                    grace=timedelta(0),
                )
                # The brief's literal fixture dropped `dry_run` here,
                # forwarding only `now` to `collector.run(...)`. That
                # makes `run_media_gc(dry_run=True)` silently perform the
                # real, destructive sweep — caught by the dry-run
                # assertion below failing with `deleted=True`.
                return await collector.run(now=datetime.now(UTC), dry_run=dry_run)

        return asyncio.run(_go())

    return run
