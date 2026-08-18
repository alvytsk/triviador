"""What the app is made of, and how a route gets at it.

`AppDependencies` is a plain frozen dataclass on `app.state`, not FastAPI's
`Depends` graph, because the composition root builds these once at startup
and every route wants the same instances. `Depends` is used only where a
*request* is the input — `current_principal` is the whole list.

Tasks 12 and 15 add fields (`hub`, `manager`, `games`, `maps`, `presets`)
as the things that fill them come into existence.
"""

from dataclasses import dataclass
from typing import Annotated

from fastapi import Depends, Request

from triviador.api.errors import ApiError, ApiErrorCode
from triviador.config import Settings
from triviador.db.security import token_digest
from triviador.services.identity import (
    AuthenticatedPrincipal,
    InviteStore,
    PasswordHasher,
    SessionStore,
    UserStore,
)
from triviador.services.ports import Clock, DatabaseProbe


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


def deps_of(request: Request) -> AppDependencies:
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
