"""The app factory. The *composition root* — which builds the real
adapters — is `build_app` below; this half only assembles routers,
handlers and middleware around a dependency bundle it is handed.

Split that way on purpose: every contract test in `tests/api/` constructs
an app over fakes, and a factory that reached for an engine could not be
called without a database.
"""

import logging
import random
import secrets
from collections.abc import AsyncIterator, Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from dataclasses import dataclass
from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory
from fastapi import FastAPI
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from triviador.api.deps import AppDependencies, Readiness
from triviador.api.errors import install_error_handlers
from triviador.api.http import auth, health, maps
from triviador.api.logging import RequestContextMiddleware, configure_logging
from triviador.api.middleware import BodyLimitMiddleware, HostMiddleware, OriginMiddleware
from triviador.api.ws import endpoint
from triviador.api.ws.broadcaster import WsBroadcaster
from triviador.api.ws.hub import Hub
from triviador.config import Settings, startup_problems
from triviador.db.engine import EnginePing, create_engine, sessionmaker_for
from triviador.db.repositories.auth import InviteRepository, SessionRepository, UserRepository
from triviador.db.repositories.games import GameRepository
from triviador.db.repositories.presets import PresetRepository
from triviador.db.security import Argon2Hasher
from triviador.db.unit_of_work import UnitOfWork
from triviador.maps.registry import MapRegistry
from triviador.runtime.clock import SystemClock
from triviador.runtime.loader import GameLoader
from triviador.runtime.manager import GameManager
from triviador.runtime.materialiser import Materialiser
from triviador.runtime.reaper import Reaper
from triviador.runtime.watchdog import Watchdog

logger = logging.getLogger(__name__)


def create_app(
    deps: AppDependencies,
    *,
    lifespan: Callable[[FastAPI], AbstractAsyncContextManager[None]] | None = None,
) -> FastAPI:
    app = FastAPI(title="Triviador", version="1", docs_url=None, redoc_url=None, lifespan=lifespan)
    app.state.deps = deps
    # Starlette applies middleware in **reverse** registration order: the
    # last one added is the outermost. So this list reads inside-out, and
    # the effective order is
    #
    #     RequestContext → Host → BodyLimit → Origin → routes
    #
    # Request-id outermost, so a refusal from any of the other three still
    # carries an id and is still logged. Host next, because a request for
    # the wrong host is not ours to reason about. Body limit before origin,
    # so an oversized body is refused without being read whatever its
    # origin.
    app.add_middleware(OriginMiddleware, allowed_origins=deps.settings.allowed_origins)
    app.add_middleware(BodyLimitMiddleware, max_bytes=deps.settings.max_body_bytes)
    app.add_middleware(HostMiddleware, allowed_hosts=deps.settings.allowed_hosts)
    app.add_middleware(RequestContextMiddleware)
    app.include_router(auth.router)
    app.include_router(maps.router)
    app.include_router(health.router)
    app.include_router(endpoint.router)
    install_error_handlers(app)
    return app


# ---------------------------------------------------------------------------
# ...and the composition root proper.
#
# `create_app` (above) is handed its dependencies; `build_app` constructs
# them. That is the split every contract test in `tests/api/` depends on —
# a factory that reached for an engine could not be called without a
# database.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class BuiltApp:
    deps: AppDependencies
    engine: AsyncEngine
    watchdog: Watchdog
    reaper: Reaper


def build_dependencies(settings: Settings) -> BuiltApp:
    engine = create_engine(settings.database_url)
    sessions = sessionmaker_for(engine)
    clock = SystemClock()
    hub = Hub()
    broadcaster = WsBroadcaster(hub, media_base=settings.media_public_base)
    maps_registry = MapRegistry(root=settings.maps_root)
    uow = UnitOfWork(sessions)
    games = GameRepository(sessions)
    rng = random.Random()

    manager = GameManager(
        loader=GameLoader(uow=uow, maps=maps_registry),
        uow=uow,
        materialiser=Materialiser(clock=clock, rng=rng),
        clock=clock,
        broadcaster=broadcaster,
        subscribers=broadcaster,
        games=games,
        rng=rng,
        queue_maxsize=settings.command_queue_maxsize,
        commit_max_attempts=settings.commit_max_attempts,
        backoff_initial_s=settings.recovery_backoff_initial_s,
        backoff_max_s=settings.recovery_backoff_max_s,
    )
    hasher = Argon2Hasher()
    deps = AppDependencies(
        settings=settings,
        clock=clock,
        hasher=hasher,
        # A fresh random secret, so the stored dummy is not a hash of a
        # value anybody can guess and test against.
        dummy_password_hash=hasher.hash(secrets.token_urlsafe(32)),
        users=UserRepository(sessions),
        sessions=SessionRepository(sessions),
        invites=InviteRepository(sessions),
        database=EnginePing(engine),
        hub=hub,
        broadcaster=broadcaster,
        manager=manager,
        readiness=Readiness(),
        games=games,
        maps=maps_registry,
        presets=PresetRepository(sessions),
    )
    return BuiltApp(
        deps=deps,
        engine=engine,
        watchdog=Watchdog(
            manager=manager,
            clock=clock,
            interval_s=settings.watchdog_interval_s,
            grace_s=settings.watchdog_grace_s,
        ),
        reaper=Reaper(
            manager=manager,
            games=games,
            # §5.6's "LOBBY with no connections → runtime may be unloaded":
            # the reaper asks the hub how many subscribers a game has, so
            # the broadcaster arrives here in its second role.
            subscribers=broadcaster,
            clock=clock,
            interval_s=settings.reaper_interval_s,
            empty_lobby_grace_minutes=settings.empty_lobby_grace_minutes,
            lobby_max_age_hours=settings.lobby_max_age_hours,
        ),
    )


def build_app(settings: Settings) -> FastAPI:
    configure_logging(log_level=settings.log_level, log_format=settings.log_format)
    problems = startup_problems(settings)
    if problems:
        # §10.4: an unconfigured deploy fails loudly rather than running
        # with a published password or an origin list that can never match.
        raise RuntimeError("configuration is invalid:\n  " + "\n  ".join(problems))

    built = build_dependencies(settings)
    return create_app(built.deps, lifespan=_lifespan(built))


def _lifespan(built: BuiltApp) -> Callable[[FastAPI], AbstractAsyncContextManager[None]]:
    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        deps, readiness = built.deps, built.deps.readiness
        async with built.engine.connect() as connection:
            current = (
                await connection.execute(text("SELECT version_num FROM alembic_version"))
            ).scalar_one_or_none()
        # §10.5: the `migrate` service runs `alembic upgrade head` before
        # this process starts. Verifying rather than migrating here is
        # deliberate — rebuilding state against an old schema is how a
        # "successful" deploy silently corrupts live games, and a server
        # that migrates on boot has no way to be told not to.
        readiness.migrations_current = current == _head_revision()
        if not readiness.migrations_current:
            raise RuntimeError(f"database is at revision {current!r}, expected head")

        unloadable = await deps.manager.recover_active_games()
        if unloadable:
            logger.error(
                "startup recovery could not load %d game(s): %s",
                len(unloadable),
                ", ".join(unloadable),
            )
        logger.info(
            "startup recovery complete",
            extra={"recovered": len(deps.manager.live_runtimes())},
        )
        readiness.recovery_complete = True

        # `start()`, not `asyncio.create_task(...)`: both classes own their
        # own task and their own `aclose()`, and a second task around them
        # would be a handle nothing else knows about.
        built.watchdog.start()
        built.reaper.start()
        try:
            yield
        finally:
            readiness.recovery_complete = False
            # `shutdown` fences first, then awaits these two closers, then
            # drains every runtime — never cancelling one mid-COMMIT (§5.6).
            # Passing them here is the whole mechanism: `SupportsAclose` is
            # exactly `aclose()`, and shutdown must stop them *before* it
            # touches a runtime, or a tick in flight re-enqueues into a
            # queue that is about to be drained.
            await deps.manager.shutdown(built.watchdog, built.reaper)
            await built.engine.dispose()

    return lifespan


def _head_revision() -> str | None:
    """Alembic's own idea of "head", read from its script directory rather
    than hardcoded — the migration files are the one place this can go
    stale without anyone editing this function."""
    backend_root = Path(__file__).resolve().parents[3]
    config = Config(str(backend_root / "alembic.ini"))
    return ScriptDirectory.from_config(config).get_current_head()
