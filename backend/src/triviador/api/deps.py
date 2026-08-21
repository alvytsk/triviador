"""What the app is made of, and how a route gets at it.

`AppDependencies` is a plain frozen dataclass on `app.state`, not FastAPI's
`Depends` graph, because the composition root builds these once at startup
and every route wants the same instances. `Depends` is used only where a
*request* is the input — `current_principal` is the whole list.

Task 16 adds `hub`, `broadcaster` and `manager` — the socket-side
collaborators `api/ws/endpoint.py` drives. Later tasks add `games`,
`maps`, `presets` the same way, as the things that fill them exist.
"""

from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Annotated, Any, Literal

from fastapi import Depends, Request
from starlette.requests import HTTPConnection

from triviador.api.errors import ApiError, ApiErrorCode
from triviador.api.schemas.ws import LobbyGame, LobbyMessage
from triviador.config import Settings
from triviador.db.security import token_digest
from triviador.services.admin import (
    CategoryPort,
    ImportPort,
    InviteAdminPort,
    MediaAssetPort,
    PresetAdminPort,
    QuestionAdminPort,
    UserAdminPort,
)
from triviador.services.identity import (
    AuthenticatedPrincipal,
    InviteStore,
    PasswordHasher,
    SessionStore,
    UserRole,
    UserStore,
)
from triviador.services.ports import Clock, DatabaseProbe, GameCatalogPort, MapProvider, PresetPort
from triviador.services.storage import ImportStagingStore, MediaStore

if TYPE_CHECKING:
    from triviador.api.ws.broadcaster import WsBroadcaster
    from triviador.api.ws.hub import Hub
    from triviador.media.pipeline import ImageNormalizer
    from triviador.runtime.manager import GameManager


class _Unusable:
    """A structural stand-in for every port `AppDependencies` declares as a
    `Protocol`. `__getattr__` hands back a callable for any name — `ping`,
    `get_by_username`, `find_unfinished`, whichever attribute a Protocol
    method the caller reaches for — and that callable raises the moment it
    is actually invoked, rather than returning `None` or silently
    no-op'ing. Existing only for `AppDependencies.placeholder()` (below):
    enough to satisfy every port's shape so `create_app` can build its
    router table, and nothing that could pass for a real answer.
    """

    def __getattr__(self, name: str) -> Callable[..., Any]:
        def _raise(*args: Any, **kwargs: Any) -> Any:
            raise RuntimeError(
                f"AppDependencies.placeholder() has no working {name}() — "
                "export_contracts builds an app but touches no database"
            )

        return _raise


@dataclass
class Readiness:
    """The two startup facts, recorded once.

    §10.6: readiness reports the *result* of the startup assertions rather
    than re-running them on every poll — that is true of the migration
    check and of recovery, both of which are settled by the time the
    process serves. It is **not** true of the database, which can go away
    while the process keeps running; that one is probed per request through
    `AppDependencies.database` (see `DatabaseProbe`).
    """

    migrations_current: bool = False
    recovery_complete: bool = False


@dataclass(frozen=True)
class AppDependencies:
    settings: Settings
    clock: Clock
    hasher: PasswordHasher
    # Argon2 over one throwaway secret, computed once during composition.
    # `login` verifies against it when the username does not exist, so both
    # failure paths perform exactly one `verify` — see `http/auth.py`.
    dummy_password_hash: str
    users: UserStore
    sessions: SessionStore
    invites: InviteStore
    invites_admin: InviteAdminPort
    users_admin: UserAdminPort
    database: DatabaseProbe
    hub: "Hub"
    broadcaster: "WsBroadcaster"
    manager: "GameManager"
    readiness: Readiness
    games: GameCatalogPort
    maps: MapProvider
    presets: PresetPort
    presets_admin: PresetAdminPort
    media_store: MediaStore
    media_assets: MediaAssetPort
    questions_admin: QuestionAdminPort
    categories: CategoryPort
    normalizer: "ImageNormalizer"
    imports: ImportPort
    staging_store: ImportStagingStore

    async def lobby_message(
        self, kind: Literal["lobby.snapshot", "lobby.update"]
    ) -> "LobbyMessage":
        return LobbyMessage(
            type=kind,
            games=tuple(
                LobbyGame(
                    game_id=str(s.game_id),
                    map_id=str(s.map_id),
                    host_id=str(s.host_id),
                    status=s.status,
                    player_count=s.player_count,
                    max_players=s.max_players,
                )
                for s in await self.games.list_joinable()
            ),
        )

    @classmethod
    def placeholder(cls) -> "AppDependencies":
        """Enough to build the router table and nothing more.

        `export_contracts` needs an app but no database (§7): `create_app`
        only wires routers and middleware around a dependency bundle — it
        never calls a port method — so this classmethod hands it one built
        from `_Unusable()` wherever a field is a `Protocol`, plus the
        handful of concrete collaborators (`Hub`, `WsBroadcaster`,
        `GameManager`, `Materialiser`) that construct without touching a
        database themselves. Kept out of `build_dependencies`'s path on
        purpose: that function builds an `AsyncEngine`; this one never does.
        """
        import random

        from triviador.api.ws.broadcaster import WsBroadcaster
        from triviador.api.ws.hub import Hub
        from triviador.media.pipeline import ImageNormalizer
        from triviador.runtime.manager import GameManager
        from triviador.runtime.materialiser import Materialiser

        settings = Settings(database_url="postgresql+asyncpg://placeholder/placeholder")
        hub = Hub()
        broadcaster = WsBroadcaster(hub, media_base=settings.media_public_base)
        unusable = _Unusable()
        manager = GameManager(
            loader=unusable,
            uow=unusable,
            materialiser=Materialiser(clock=unusable, rng=random.Random()),
            clock=unusable,
            broadcaster=broadcaster,
            subscribers=broadcaster,
            games=unusable,
            rng=random.Random(),
        )
        return cls(
            settings=settings,
            clock=unusable,
            hasher=unusable,
            dummy_password_hash="",
            users=unusable,
            sessions=unusable,
            invites=unusable,
            invites_admin=unusable,
            users_admin=unusable,
            database=unusable,
            hub=hub,
            broadcaster=broadcaster,
            manager=manager,
            readiness=Readiness(),
            games=unusable,
            maps=unusable,
            presets=unusable,
            presets_admin=unusable,
            media_store=unusable,
            media_assets=unusable,
            questions_admin=unusable,
            categories=unusable,
            normalizer=ImageNormalizer(max_bytes=1, max_pixels=1, target_px=1),
            imports=unusable,
            staging_store=unusable,
        )


def deps_of(request: HTTPConnection) -> AppDependencies:
    """`HTTPConnection`, not `Request`: `Request` and `WebSocket` are
    siblings under it (neither is a subtype of the other), and the `/ws`
    endpoint calls this the same way a route's `Depends(deps_of)` does —
    `app.state` is reachable from both."""
    deps: AppDependencies = request.app.state.deps
    return deps


async def optional_principal(request: Request) -> AuthenticatedPrincipal | None:
    deps = deps_of(request)
    token = request.cookies.get(deps.settings.session_cookie_name)
    if not token:
        return None
    return await deps.sessions.resolve(token_digest(token), now=deps.clock.now())


async def current_principal(
    principal: Annotated[AuthenticatedPrincipal | None, Depends(optional_principal)],
) -> AuthenticatedPrincipal:
    if principal is None:
        raise ApiError(ApiErrorCode.UNAUTHENTICATED, 401, "not signed in")
    return principal


Principal = Annotated[AuthenticatedPrincipal, Depends(current_principal)]
Deps = Annotated[AppDependencies, Depends(deps_of)]


async def current_admin(principal: Principal) -> AuthenticatedPrincipal:
    """403, not 404.

    Spec 1B §9 makes `/admin/*` a lazily-loaded, role-guarded tree — the
    client already knows the routes exist, because it decides whether to
    load them from `Me.role`. Hiding them behind a 404 for a player would
    buy nothing and would make a genuine typo indistinguishable from a
    permission problem in the one place an operator debugs by curl.
    """
    if principal.role is not UserRole.ADMIN:
        raise ApiError(ApiErrorCode.FORBIDDEN, 403, "administrator access required")
    return principal


AdminPrincipal = Annotated[AuthenticatedPrincipal, Depends(current_admin)]
