"""The guard is a property of the router, not of any route.

A per-route `Depends(current_admin)` is one `git` conflict away from being
dropped from a single handler, and the failure is silent — the route keeps
working, for everybody. `test_every_admin_route_is_guarded` walks the real
app instead of trusting the source, over every route the whole admin
surface (`http/admin/`) mounts today — categories, questions, media,
imports, invites, users, presets — not a vacuous check with nothing yet
to find: `test_the_walk_sees_a_route_mounted_the_way_every_admin_route_will_be`
and the two "caught" tests below prove the walk actually fails when a
route or a router loses its guard.
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


# Deliberately distinct from `probe`: `probe`'s handler takes `principal:
# AdminPrincipal`, so `current_admin` is already in its own `dependant`
# from the parameter alone, independent of any router `dependencies=`.
# Using `probe` to test whether a *router's* guard is what protects a
# route would never observe a missing guard — the parameter protects it
# regardless. `bare` carries no guard anywhere, so it is only ever
# protected by whichever wrapper it is included into.
bare = APIRouter()


@bare.get("/probe")
async def _bare_probe() -> dict[str, str]:
    return {}


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
    """Every `/api/admin` path that `current_admin` does not protect.

    Both halves matter. A route can carry the guard in its own dependency
    tree (a per-route `Depends`) or inherit it from the router it was
    included into (`build_admin_router`'s `dependencies=`), and in FastAPI
    0.141 those are two different places — checking only the first reports
    every correctly-guarded admin route as unguarded, and checking only the
    second misses a route mounted some other way.
    """
    return [
        mounted.path
        for mounted in api_routes(app)
        if mounted.path.startswith("/api/admin")
        and current_admin not in (mounted.guards | _dependency_calls(mounted.route))
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
    paths = {mounted.path for mounted in api_routes(create_app(deps))}
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


def test_the_walk_sees_a_route_mounted_the_way_every_admin_route_will_be(
    deps: AppDependencies,
) -> None:
    """The topology every later task uses: a prefix-less sub-router,
    included into `build_admin_router`, included into the app.

    Its raw `APIRoute.path` is `/probe` — the `/api/admin` half lives on the
    include context — and its guard is on that include context too, not in
    its `dependant`. A walk that gets either wrong reports this route as
    absent or as unguarded, and both failures are silent.

    Uses `bare`, not `probe`: `probe`'s own handler already carries
    `current_admin` via its `AdminPrincipal` parameter, so it would stay
    "guarded" even if `build_admin_router` lost its `dependencies=`
    entirely — proven by running this test with that line deleted and
    watching it still pass. `bare` has no guard of its own, so this
    assertion is actually exercising `build_admin_router`'s router-level
    dependency, not standing next to it.
    """
    app = create_app(deps)
    app.include_router(build_admin_router(bare))
    assert "/api/admin/probe" in {mounted.path for mounted in api_routes(app)}
    assert unguarded_admin_routes(app) == []


def test_a_sub_router_mounted_without_the_guard_is_caught(deps: AppDependencies) -> None:
    """The same topology, minus the guard: a bare `APIRouter(prefix=...)`
    used in place of `build_admin_router`. This is the mistake the check
    exists for, and it is not the same mistake as the rogue route — that one
    bypasses the wrapper, this one builds the wrapper wrongly.

    Deliberately `bare`, not `probe`: `probe`'s handler takes `principal:
    AdminPrincipal`, so `current_admin` is already in its own `dependant`
    from the parameter alone, independent of any router `dependencies=`.
    Mounting `probe` here would leave it guarded no matter what this test
    does to the wrapper, and the assertion below could never observe the
    failure it exists to catch. `bare` carries no guard anywhere — not on
    a parameter, not on a router — so it is only ever protected by
    whichever wrapper it is included into, which is the one thing this
    test varies.
    """
    unguarded_wrapper = APIRouter(prefix="/api/admin")
    unguarded_wrapper.include_router(bare)
    app = create_app(deps)
    app.include_router(unguarded_wrapper)
    assert unguarded_admin_routes(app) == ["/api/admin/probe"]


def _dependency_calls(route: APIRoute) -> set[object]:
    """Every callable in the route's *own* dependency tree — a handler's
    parameter-level `Depends()`, walked recursively since
    `current_principal` sits one level below `current_admin`.

    Router-level included, despite the name suggesting otherwise: it is
    not. In FastAPI 0.141, a router's `dependencies=` lives on
    `_IncludedRouter.include_context.dependencies`, a place this walk
    never reaches, and is never merged into the route's own `Dependant` —
    confirmed against this project's own app, not assumed from the
    library's docs. That is exactly why `unguarded_admin_routes` unions
    this function's result with `mounted.guards` rather than trusting
    either alone: this walk is what catches `probe`'s guard (carried on
    its own handler parameter), and `mounted.guards` is what catches
    `bare`'s (inherited only from `build_admin_router`'s `dependencies=`).
    Relying on this function by itself would report every router-guarded
    admin route as unguarded — the exact failure that hid the real gap
    this module now catches.
    """
    calls: set[object] = set()
    stack = [route.dependant]
    while stack:
        dependant = stack.pop()
        calls.add(dependant.call)
        stack.extend(dependant.dependencies)
    return calls
