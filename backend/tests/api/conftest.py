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

import httpx
import pytest
import pytest_asyncio
import structlog

from tests.api.fakes import (
    FakeClock,
    FakeDatabase,
    FakeHasher,
    FakeInvites,
    FakeSessions,
    FakeUsers,
)
from triviador.api.app import create_app
from triviador.api.deps import AppDependencies
from triviador.config import Settings


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
def deps(settings: Settings, users: FakeUsers) -> AppDependencies:
    hasher = FakeHasher()
    return AppDependencies(
        settings=settings,
        clock=FakeClock(),
        hasher=hasher,
        dummy_password_hash=hasher.hash("nobody"),
        users=users,
        sessions=FakeSessions(users),
        invites=FakeInvites(users),
        database=FakeDatabase(),
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
