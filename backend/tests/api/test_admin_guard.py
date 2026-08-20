"""The guard is a property of the router, not of any route.

A per-route `Depends(current_admin)` is one `git` conflict away from being
dropped from a single handler, and the failure is silent — the route keeps
working, for everybody. `test_every_admin_route_is_guarded` walks the real
app instead of trusting the source: it is vacuous while `http/admin/` holds
no routes, and it covers Task 4's first one automatically.
"""

import httpx
import pytest
from fastapi import APIRouter, FastAPI
from fastapi.routing import APIRoute

from tests.api.conftest import ORIGIN, _seed_admin, api_routes
from triviador.api.app import create_app
from triviador.api.deps import AdminPrincipal, AppDependencies, current_admin
from triviador.api.http.admin import build_admin_router

probe = APIRouter()


@probe.get("/probe")
async def _probe(principal: AdminPrincipal) -> dict[str, str]:
    return {"user_id": str(principal.user_id)}


@pytest.fixture
def probe_app(deps: AppDependencies) -> object:
    app = create_app(deps)
    app.include_router(build_admin_router(probe))
    return app


async def _get(app: object, cookie: str | None) -> httpx.Response:
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)  # type: ignore[arg-type]
    async with httpx.AsyncClient(
        transport=transport, base_url="http://testserver", headers={"Origin": ORIGIN}
    ) as client:
        if cookie is not None:
            client.cookies.set("triviador_session", cookie)
        return await client.get("/api/admin/probe")


async def test_anonymous_is_unauthenticated(probe_app: object) -> None:
    response = await _get(probe_app, None)
    assert response.status_code == 401
    assert response.json()["code"] == "unauthenticated"


async def test_a_player_is_forbidden(probe_app: object) -> None:
    """401 and 403 are different facts: the first says sign in, the second
    says signing in again will not help."""
    response = await _get(probe_app, "tok")
    assert response.status_code == 403
    assert response.json()["code"] == "forbidden"


async def test_an_admin_gets_through(probe_app: object, deps: AppDependencies) -> None:
    await _seed_admin(deps)
    response = await _get(probe_app, "tok-admin")
    assert response.status_code == 200
    assert response.json() == {"user_id": "admin"}


def unguarded_admin_routes(app: FastAPI) -> list[str]:
    """Every `/api/admin` path whose dependency tree lacks `current_admin`."""
    return [
        route.path
        for route in api_routes(app)
        if route.path.startswith("/api/admin") and current_admin not in _dependency_calls(route)
    ]


def test_the_walk_reaches_real_routes(deps: AppDependencies) -> None:
    """The self-check, and the reason it exists.

    `app.routes` does **not** contain `APIRoute` objects in the FastAPI
    this project pins (0.141.1): `include_router` appends an
    `_IncludedRouter` wrapper and resolves lazily, so the obvious
    `[r for r in app.routes if isinstance(r, APIRoute)]` yields an empty
    list — and every "no unguarded routes" assertion built on it passes
    forever, including for a route with no guard at all.

    So this module asserts that its own walk finds something known before
    any test asserts what the walk did not find. A detector that returns
    nothing is indistinguishable from a codebase with nothing to detect.
    """
    paths = {route.path for route in api_routes(create_app(deps))}
    assert "/api/games" in paths
    assert len(paths) >= 10


def test_every_admin_route_is_guarded(deps: AppDependencies) -> None:
    assert unguarded_admin_routes(create_app(deps)) == []


def test_the_check_sees_an_unguarded_admin_route(deps: AppDependencies) -> None:
    """A guard nobody has watched fail is a guard nobody can trust — the
    same discipline `tests/test_layering.py` applies to its import gates.

    The rogue router is mounted directly on the app, bypassing
    `build_admin_router`, which is precisely how a future task would
    introduce the hole this check exists to catch.
    """
    rogue = APIRouter(prefix="/api/admin")

    @rogue.get("/rogue")
    async def _rogue() -> dict[str, str]:
        return {}

    app = create_app(deps)
    app.include_router(rogue)
    assert unguarded_admin_routes(app) == ["/api/admin/rogue"]


def _dependency_calls(route: APIRoute) -> set[object]:
    """Every callable in the route's dependency tree, router-level included.

    FastAPI merges a router's `dependencies=` into each route's
    `Dependant`, so a structural check can see them — but only by walking,
    since `current_principal` sits one level below `current_admin`.
    """
    calls: set[object] = set()
    stack = [route.dependant]
    while stack:
        dependant = stack.pop()
        calls.add(dependant.call)
        stack.extend(dependant.dependencies)
    return calls
