"""`configure_logging` mutates two pieces of process-global state:
`structlog.configure(...)` and `logging.basicConfig(...)`. Both persist
past the end of whichever test called them — there is no `structlog`
un-configure and `basicConfig` only ever adds a handler, never removes
one — so without this fixture, `test_logging.py`'s tests would leave every
later test in the session logging JSON to stdout through a leftover
`StreamHandler`, whether or not that later test wanted JSON logging at all.

This is a plain (non-yielding-until-teardown-matters) autouse fixture in
this directory's `conftest.py`, not in `test_logging.py` itself: pytest
instantiates a parent conftest's autouse fixture *before* a same-scope
autouse fixture declared in the test module, and tears it down *after* —
confirmed empirically, not assumed — so the snapshot taken here is the
pristine pre-`configure_logging` state, and the restore runs after
whatever `test_logging.py`'s own `json_logging` fixture did.
"""

import logging
from collections.abc import AsyncIterator, Iterator
from dataclasses import replace as _replace
from datetime import timedelta
from pathlib import Path

import httpx
import pytest
import pytest_asyncio
import structlog

from tests.api.fakes import (
    FakeClock,
    FakeDatabase,
    FakeGameCatalog,
    FakeHasher,
    FakeInvites,
    FakePresets,
    FakeSessions,
    FakeUsers,
)
from tests.conftest import lobby_state
from tests.runtime.conftest import manager_with_resident
from tests.runtime.fakes import T0
from tests.runtime.fakes import FakeClock as RuntimeFakeClock
from tests.runtime.integration.conftest import write_grid_map
from triviador.api.app import create_app
from triviador.api.deps import AppDependencies, Readiness
from triviador.api.ws.broadcaster import WsBroadcaster
from triviador.api.ws.hub import Hub
from triviador.config import Settings
from triviador.db.security import token_digest
from triviador.domain.ids import SessionId, UserId
from triviador.maps.registry import MapRegistry
from triviador.services.identity import UserRole


@pytest.fixture(autouse=True)
def _restore_logging_globals() -> Iterator[None]:
    structlog_config = structlog.get_config()
    root = logging.getLogger()
    handlers = list(root.handlers)
    level = root.level
    yield
    structlog.configure(**structlog_config)
    root.handlers[:] = handlers
    root.setLevel(level)


ORIGIN = "http://box.lan"


@pytest.fixture
def settings() -> Settings:
    return Settings(
        database_url="postgresql+asyncpg://unused/unused",
        allowed_origins=(ORIGIN,),
        allowed_hosts=("testserver", "box.lan"),
        cookie_secure=False,
    )


@pytest.fixture
def users() -> FakeUsers:
    return FakeUsers()


@pytest.fixture
def map_root(tmp_path: Path) -> Path:
    """Reuses `tests/runtime/integration/conftest.write_grid_map` so
    `deps.maps` is a real `MapRegistry` over a real `map.json`, not a
    fake — `MapRegistry` is a thin, direct-to-filesystem adapter with no
    port of its own worth faking (Task 17)."""
    write_grid_map(tmp_path / "grid")
    return tmp_path


def replace_deps(deps: AppDependencies, **overrides: object) -> AppDependencies:
    return _replace(deps, **overrides)  # type: ignore[arg-type]


@pytest_asyncio.fixture
async def deps(settings: Settings, users: FakeUsers, map_root: Path) -> AppDependencies:
    """One signed-in user, `u1`, whose cookie value is the literal `"tok"`,
    and one live game they are a player in. Every socket test starts from
    "authenticated participant" and takes away whatever it is testing."""
    clock = FakeClock()
    sessions = FakeSessions(users)
    hasher = FakeHasher()
    await users.create(
        user_id=UserId("u1"),
        username="u1",
        password_hash=hasher.hash("correct horse"),
        display_name="U1",
        role=UserRole.PLAYER,
    )
    await sessions.create(
        session_id=SessionId("s1"),
        user_id=UserId("u1"),
        token_hash=token_digest("tok"),
        expires_at=clock.now() + timedelta(days=30),
    )
    hub = Hub()
    # `start=False`: this suite is about the endpoint's own authorization and
    # frame-to-command translation, not the runtime's execution pipeline
    # (Task 8/9's job) — a started runtime races its own consumer task
    # against `queued_commands()`'s peek the moment `serve_connection`
    # awaits anything (its `finally` always does), and `StubExecutor([])`'s
    # empty outcome list turns that race into a logged consumer failure.
    manager, _ = manager_with_resident(
        lobby_state({"u1": 0, "u2": 1}), RuntimeFakeClock(T0), start=False
    )
    return AppDependencies(
        settings=settings,
        clock=clock,
        hasher=hasher,
        dummy_password_hash=hasher.hash("nobody"),
        users=users,
        sessions=sessions,
        invites=FakeInvites(users),
        database=FakeDatabase(),
        hub=hub,
        broadcaster=WsBroadcaster(hub, media_base=settings.media_public_base),
        manager=manager,
        readiness=Readiness(migrations_current=True, recovery_complete=True),
        games=FakeGameCatalog(),
        maps=MapRegistry(root=map_root),
        presets=FakePresets(),
    )


@pytest_asyncio.fixture
async def client(deps: AppDependencies) -> AsyncIterator[httpx.AsyncClient]:
    """`headers={"Origin": ORIGIN}` on every request by default: origin
    checking (Task 8) applies to unsafe methods, and a suite that omitted it
    would test the 403 path by accident on every POST. `test_origin.py`
    overrides it deliberately."""
    transport = httpx.ASGITransport(app=create_app(deps), raise_app_exceptions=False)
    async with httpx.AsyncClient(
        transport=transport, base_url="http://testserver", headers={"Origin": ORIGIN}
    ) as client:
        yield client


@pytest_asyncio.fixture
async def signed_in(
    client: httpx.AsyncClient, settings: Settings
) -> AsyncIterator[httpx.AsyncClient]:
    """`client`, carrying `u1`'s session cookie (see `deps`'s fixture
    docstring: `"tok"` resolves to `u1` via `FakeSessions`)."""
    client.cookies.set(settings.session_cookie_name, "tok")
    yield client
