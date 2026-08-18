"""What the app is made of, and how a route gets at it.

`AppDependencies` is a plain frozen dataclass on `app.state`, not FastAPI's
`Depends` graph, because the composition root builds these once at startup
and every route wants the same instances. `Depends` is used only where a
*request* is the input — `current_principal` is the whole list.

Task 16 adds `hub`, `broadcaster` and `manager` — the socket-side
collaborators `api/ws/endpoint.py` drives. Later tasks add `games`,
`maps`, `presets` the same way, as the things that fill them exist.
"""

from dataclasses import dataclass
from typing import TYPE_CHECKING, Annotated, Literal

from fastapi import Depends, Request
from starlette.requests import HTTPConnection

from triviador.api.errors import ApiError, ApiErrorCode
from triviador.api.schemas.ws import LobbyMessage
from triviador.config import Settings
from triviador.db.security import token_digest
from triviador.services.identity import (
    AuthenticatedPrincipal,
    InviteStore,
    PasswordHasher,
    SessionStore,
    UserStore,
)
from triviador.services.ports import Clock, DatabaseProbe, GameCatalogPort, MapProvider, PresetPort

if TYPE_CHECKING:
    from triviador.api.ws.broadcaster import WsBroadcaster
    from triviador.api.ws.hub import Hub
    from triviador.runtime.manager import GameManager


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
    database: DatabaseProbe
    hub: "Hub"
    broadcaster: "WsBroadcaster"
    manager: "GameManager"
    readiness: Readiness
    games: GameCatalogPort
    maps: MapProvider
    presets: PresetPort

    async def lobby_message(
        self, kind: Literal["lobby.snapshot", "lobby.update"]
    ) -> "LobbyMessage":
        """Overridden in Task 18, when there is a catalog to read. Until
        then an empty lobby is honest: nothing can create a game yet."""
        return LobbyMessage(type=kind, games=())


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
