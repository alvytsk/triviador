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
import random
from collections.abc import AsyncIterator, Iterator
from dataclasses import dataclass
from dataclasses import replace as _replace
from datetime import timedelta
from decimal import Decimal
from pathlib import Path
from typing import NamedTuple

import httpx
import pytest
import pytest_asyncio
import structlog
from fastapi import FastAPI
from fastapi.routing import APIRoute

from tests.api.fakes import (
    FakeCategories,
    FakeClock,
    FakeDatabase,
    FakeGameCatalog,
    FakeHasher,
    FakeImports,
    FakeInvites,
    FakeMediaAssets,
    FakeMediaStore,
    FakePresets,
    FakeQuestionAdmin,
    FakeSessions,
    FakeStagingStore,
    FakeUserAdmin,
    FakeUsers,
    RecordingHub,
)
from tests.conftest import lobby_state
from tests.runtime.conftest import StubExecutor, _created_managers, _NoGameQueries, a_manager
from tests.runtime.fakes import T0, FakeBroadcaster
from tests.runtime.fakes import FakeClock as RuntimeFakeClock
from tests.runtime.integration.conftest import write_grid_map
from tests.runtime.test_commit import FakeUnitOfWork
from triviador.api.app import create_app
from triviador.api.deps import AppDependencies, Readiness
from triviador.api.ws.broadcaster import WsBroadcaster
from triviador.config import Settings
from triviador.db.security import token_digest
from triviador.domain.game.rules import GameRules
from triviador.domain.game.state import GameState
from triviador.domain.ids import GameId, MapId, PlayerId, SessionId, UserId
from triviador.maps.registry import MapRegistry
from triviador.media.pipeline import ImageNormalizer
from triviador.runtime.errors import PermanentReplayFailure
from triviador.runtime.manager import Live
from triviador.runtime.materialiser import Materialiser
from triviador.runtime.runtime import GameRuntime
from triviador.services.admin import QuestionDetailRecord
from triviador.services.identity import UserRole
from triviador.services.ports import GameSummary


@dataclass
class _GenesisLoader:
    """Stands in for `runtime.loader.GameLoader`, for a game this suite's
    `FakeGameCatalog` created directly (§6.2's `tx1`) rather than through a
    real event log the loader could replay.

    Returns the same *shape* one folded `GameCreated` event produces — an
    empty `Phase.LOBBY` at `seq=1`, under the map and rules `create()` was
    called with — using the map+rules the catalog recorded. `g1`, the
    fixture's own pre-seeded live game, never reaches this: it is inserted
    into the manager's registry directly, and `get()` returns that cached
    `Live` entry before ever asking a loader.
    """

    games: FakeGameCatalog

    async def load(self, game_id: GameId) -> GameState:
        for created in self.games.created:
            if created["game_id"] == game_id:
                rules = created["rules"]
                if not isinstance(rules, GameRules):
                    raise AssertionError("games.create called without rules")
                return _replace(lobby_state(players={}, rules=rules), game_id=game_id, seq=1)
        raise PermanentReplayFailure(f"no such game: {game_id}")


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


@pytest.fixture(autouse=True)
async def _close_started_runtimes() -> AsyncIterator[None]:
    """This suite's own copy of `tests/runtime/conftest.py`'s fixture of the
    same name, for the same reason: `deps` calls `a_manager`, which
    registers into the *same* module-level `_created_managers` list that
    fixture drains — but only for tests collected under `tests/runtime/`.

    Running the whole suite together, that works by accident: `api`
    collects before `runtime` (alphabetically), so every manager an `api`
    test appends is still sitting in the list, undrained, when the first
    `runtime` test's teardown empties it. `pytest tests/api` standalone
    never reaches a `tests/runtime` teardown at all, so nothing ever closes
    the resident runtime's consumer task `manager.get()` started — a parked
    task leaked at session end. Draining locally, right after each test
    that might have populated the list, removes the dependency on
    collection order entirely."""
    yield
    managers, _created_managers[:] = list(_created_managers), []
    for manager in managers:
        for runtime in manager.live_runtimes():
            if not runtime.closed:
                await runtime.aclose()


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


class MountedRoute(NamedTuple):
    """One route as the app actually serves it.

    Three fields because FastAPI 0.141 splits what used to be one object:
    `route` is the raw `APIRoute`, `path` is where it is *reachable* (the
    raw `route.path` carries only its own router's prefix), and `guards`
    are the dependencies its ancestors impose (a router-level
    `dependencies=[...]` never reaches the route's own `dependant`).
    """

    path: str
    route: APIRoute
    guards: frozenset[object]


def api_routes(app: FastAPI) -> tuple[MountedRoute, ...]:
    """Every route the app can serve, with its real path and its inherited guards.

    Three facts about FastAPI 0.141's lazy `include_router`, each verified
    against this project's own app rather than assumed, and each one a
    silent-pass bug if you get it wrong:

    1. `app.routes` holds `_IncludedRouter` wrappers, not `APIRoute`s. The
       obvious `isinstance(r, APIRoute)` filter over it returns **nothing**,
       so any "no bad routes found" assertion built on it passes forever.
    2. A route's own `path` carries only the prefix of the router it was
       defined on. Mounting a `/questions` router inside a `/api/admin`
       router leaves the raw path at `/questions`; the missing half lives on
       `include_context.prefix`.
    3. A router-level `dependencies=[Depends(current_admin)]` — the entire
       mechanism of the admin guard — is **not** merged into the route's
       `dependant`. It lives on `include_context.dependencies`, which is why
       `guards` is collected separately here.

    Reading three private attributes is the price of the check being real.
    `test_the_walk_reaches_real_routes` is the tripwire: if a FastAPI
    upgrade renames any of them, it fails loudly rather than letting the
    gates pass quietly.
    """
    found: list[MountedRoute] = []
    stack: list[tuple[object, str, frozenset[object]]] = [(app.router, "", frozenset())]
    while stack:
        router, base, guards = stack.pop()
        for route in getattr(router, "routes", ()):
            if isinstance(route, APIRoute):
                found.append(MountedRoute(base + route.path, route, guards))
            included = getattr(route, "original_router", None)
            if included is None:
                continue
            context = getattr(route, "include_context", None)
            prefix = getattr(context, "prefix", "") or ""
            inherited = tuple(getattr(context, "dependencies", ()) or ())
            stack.append(
                (included, base + prefix, guards | {d.dependency for d in inherited})
            )
    return tuple(found)


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
    hub = RecordingHub()
    games = FakeGameCatalog()
    runtime_clock = RuntimeFakeClock(T0)
    # A `GameManager` whose `_load` path (Task 18's `/api/games` routes) can
    # actually commit: `_GenesisLoader` plays the genesis event `tx1` wrote
    # directly rather than through the (real, database-backed) `GameLoader`,
    # and `FakeUnitOfWork` + a real `Materialiser` are `tests/runtime/
    # test_commit.py`'s own in-memory `CommandExecutor` collaborators — the
    # runtime path is real, only storage is not (Task 18's brief).
    manager = a_manager(
        _GenesisLoader(games),
        uow=FakeUnitOfWork(),
        materialiser=Materialiser(clock=runtime_clock, rng=random.Random(0)),
        clock=runtime_clock,
        games=_NoGameQueries(),
    )
    # `g1`, inserted directly rather than through the manager's own loader:
    # a pre-existing live game `u1` is already seated in, for every socket
    # test that starts from "authenticated participant" and takes away
    # whatever it is testing. Not started: this suite is about the
    # endpoint's own authorization and frame-to-command translation, not
    # the runtime's execution pipeline (Task 8/9's job) — a started runtime
    # races its own consumer task against `queued_commands()`'s peek the
    # moment `serve_connection` awaits anything (its `finally` always
    # does), and `StubExecutor([])`'s empty outcome list turns that race
    # into a logged consumer failure.
    resident = GameRuntime(
        state=lobby_state({"u1": 0, "u2": 1}),
        executor=StubExecutor([]),
        clock=runtime_clock,
        broadcaster=FakeBroadcaster(),
        on_fault=lambda rt, exc: None,
        generation=1,
        rng=random.Random(0),
    )
    # `g1` is inserted directly into the manager's registry above rather
    # than through `games.create`, but the WS endpoint's own `not_found`
    # guard (mirroring the REST routes') checks `games.get_summary` before
    # ever touching the manager — so the catalog needs to know about `g1`
    # too, or every socket test that subscribes to it would see a spurious
    # `not_found`.
    games.summaries[resident.game_id] = GameSummary(
        game_id=resident.game_id,
        map_id=MapId("grid"),
        host_id=PlayerId("u1"),
        status="active",
        max_players=2,
        player_count=2,
        created_at=T0,
    )
    manager._entries[resident.game_id] = Live(resident)
    media_assets = FakeMediaAssets()
    questions_admin = FakeQuestionAdmin(
        {
            "q1": QuestionDetailRecord(
                question_id="q1",
                kind="numeric",
                prompt="How many players does a default game seat?",
                category_id="cat-1",
                category_slug="general",
                difficulty="easy",
                is_active=True,
                version=1,
                media_asset_id=None,
                choices=None,
                numeric_answer=Decimal("3"),
                unit=None,
            )
        }
    )
    categories = FakeCategories()
    # One instance for both ports — `deps.invites` (the public redeem path)
    # and `deps.invites_admin` (this task) — mirroring the real
    # `InviteRepository`, which also satisfies both.
    invites = FakeInvites(users)
    presets = FakePresets()
    return AppDependencies(
        settings=settings,
        clock=clock,
        hasher=hasher,
        dummy_password_hash=hasher.hash("nobody"),
        users=users,
        sessions=sessions,
        invites=invites,
        invites_admin=invites,
        users_admin=FakeUserAdmin(users, sessions),
        database=FakeDatabase(),
        hub=hub,
        broadcaster=WsBroadcaster(hub, media_base=settings.media_public_base),
        manager=manager,
        readiness=Readiness(migrations_current=True, recovery_complete=True),
        games=games,
        maps=MapRegistry(root=map_root),
        presets=presets,
        presets_admin=presets,
        media_store=FakeMediaStore(clock),
        media_assets=media_assets,
        questions_admin=questions_admin,
        categories=categories,
        normalizer=ImageNormalizer(
            max_bytes=settings.media_max_bytes,
            max_pixels=settings.media_max_pixels,
            target_px=settings.media_target_px,
        ),
        # The same `categories`/`questions_admin`/`media_assets` instances,
        # not fresh ones: `apply_if_confirmable` writes straight into them
        # (see `FakeImports`'s docstring), and a test asserting on
        # `deps.questions_admin.records` after a confirm has to be looking
        # at the store the import actually wrote to.
        imports=FakeImports(
            categories=categories, questions_admin=questions_admin, media_assets=media_assets
        ),
        staging_store=FakeStagingStore(),
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


async def _second_client(
    deps: AppDependencies,
    settings: Settings,
    *,
    user_id: str,
    token: str,
    role: UserRole = UserRole.PLAYER,
) -> AsyncIterator[httpx.AsyncClient]:
    """A second `httpx.AsyncClient` over the same `deps` (and so the same
    app, manager and hub) carrying a *different* user's session cookie —
    the shape `other_client` and `stranger_client` share."""
    await deps.users.create(
        user_id=UserId(user_id),
        username=user_id,
        password_hash=deps.hasher.hash("correct horse"),
        display_name=user_id.upper(),
        role=role,
    )
    await deps.sessions.create(
        session_id=SessionId(f"s-{user_id}"),
        user_id=UserId(user_id),
        token_hash=token_digest(token),
        expires_at=deps.clock.now() + timedelta(days=30),
    )
    transport = httpx.ASGITransport(app=create_app(deps), raise_app_exceptions=False)
    async with httpx.AsyncClient(
        transport=transport, base_url="http://testserver", headers={"Origin": ORIGIN}
    ) as second:
        second.cookies.set(settings.session_cookie_name, token)
        yield second


@pytest_asyncio.fixture
async def other_client(
    deps: AppDependencies, settings: Settings
) -> AsyncIterator[httpx.AsyncClient]:
    """A second signed-in user, `u2`, over the same `deps` as `client`."""
    async for c in _second_client(deps, settings, user_id="u2", token="tok2"):
        yield c


@pytest_asyncio.fixture
async def stranger_client(
    deps: AppDependencies, settings: Settings
) -> AsyncIterator[httpx.AsyncClient]:
    """A third signed-in user, `u3`, who never joins anything."""
    async for c in _second_client(deps, settings, user_id="u3", token="tok3"):
        yield c


async def _seed_admin(deps: AppDependencies) -> None:
    """`admin` / `"tok-admin"`. Separate from `_second_client` because the
    guard tests need the user without needing a client."""
    await deps.users.create(
        user_id=UserId("admin"),
        username="admin",
        password_hash=deps.hasher.hash("correct horse"),
        display_name="Admin",
        role=UserRole.ADMIN,
    )
    await deps.sessions.create(
        session_id=SessionId("s-admin"),
        user_id=UserId("admin"),
        token_hash=token_digest("tok-admin"),
        expires_at=deps.clock.now() + timedelta(days=30),
    )


@pytest_asyncio.fixture
async def admin_client(
    deps: AppDependencies, settings: Settings
) -> AsyncIterator[httpx.AsyncClient]:
    """`client`, signed in as an admin. Every `/api/admin` test starts here
    and takes away whatever it is testing."""
    async for c in _second_client(
        deps, settings, user_id="admin", token="tok-admin", role=UserRole.ADMIN
    ):
        yield c
