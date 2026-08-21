# Triviador Plan 7A — Admin Backend Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give the question bank, the media it shows, the invites that create players, the users that hold them and the presets that shape a game a write path — over HTTP, behind one guard, with every byte of uploaded media re-encoded and every bulk import either applied whole or not at all. After this plan the backend is complete for Spec 1; Plan 7B renders the six admin screens, Plan 8 deploys the stack.

**Architecture:** One router tree (`/api/admin/*`) mounted behind a router-level `role == 'admin'` dependency, over repositories that follow Plan 3's shape (one `async_sessionmaker`, one transaction per public method). Two new object-storage ports live in `services/`; their aioboto3 adapters live in a new top-level `storage/` package, alongside `media/` and `imports/` — concrete packages in the same position `maps/` already holds, because `services/` holds no implementations. The pipeline that turns an upload into a WebP blob is CPU-bound and runs in `asyncio.to_thread` behind a semaphore of one, because ADR-002 puts a 200-image import in the same process as live games.

**Tech Stack:** Python 3.13 · FastAPI · SQLAlchemy 2 (async) + asyncpg · Alembic · Pydantic v2 · **aioboto3** (new) · **Pillow** (new) · pytest + pytest-asyncio · PostgreSQL 17 · **Garage** (new, test container)

**Spec:** `docs/superpowers/specs/2026-08-07-triviador-spec1-design.md` §10 (admin) and §7 (persistence), plus `docs/superpowers/specs/2026-08-16-triviador-app-architecture-design.md` §9 (admin), §6.1 (REST surface), §6.3 (error envelope), §4.1 (schema).

---

## Global Constraints

- **Python 3.13, `mypy --strict` over `src/triviador` and `tests`.** No `Any` that is not already there; `ruff` with `E,F,I,UP,B,SIM,RUF`, line length 100.
- **`services/` holds Protocols and dataclasses only.** `tests/test_layering.py` enforces that it names neither `db`, `runtime`, `api`, nor SQLAlchemy. Adapters go in `db/`, `storage/`, `media/`, `imports/`.
- **`domain/` is untouched by this plan.** Not one file under `src/triviador/domain/` is created or modified. If a task appears to need one, stop — it is a design error, not a step.
- **Every route under `/api/admin` is guarded at the router, never per-route.** A route that forgets `Depends` is a hole; Task 1 makes forgetting impossible and tests it by walking `app.routes`.
- **One transaction per admin mutation.** A repository method either does its whole job in one `async with session.begin()` or it is two methods.
- **Every semantic edit to a question bumps `questions.version`** — prompt, choices, correct answer, category, difficulty, media, unit. This is a locking invariant, not bookkeeping (`db/repositories/questions.py`'s module docstring). Toggling `is_active` does not bump it.
- **Uploaded media is re-encoded, never passed through.** SVG is rejected outright for question media (Spec 1 §10.4). The map SVG is a repository file and is not affected.
- **Blobs are content-addressed and written before the transaction that references them.** A failed transaction leaves an unreferenced blob for `media-gc`; the reverse order leaves a row pointing at nothing.
- **Error codes come from the closed `ApiErrorCode` enum.** New codes are added to the enum and exported by `export-contracts`; a route never invents a string.
- **No frontend work.** Plan 7B owns `/admin/*`, shadcn/ui, and every screen. This plan's only frontend-facing artifact is `contracts/admin.schema.json`.
- **Tests that need PostgreSQL or Garage carry `pytestmark = pytest.mark.integration`** and live under a directory whose `conftest.py` fails collection if they forget (the pattern in `tests/db/conftest.py`).

---

## File Structure

```
backend/src/triviador/
├── api/
│   ├── deps.py                          MODIFY  AdminPrincipal; media/import/store fields
│   ├── errors.py                        MODIFY  six new ApiErrorCode values
│   ├── middleware.py                    MODIFY  BodyLimitMiddleware exempt paths
│   ├── app.py                           MODIFY  mount admin router; build new adapters
│   ├── contracts.py                     MODIFY  admin.schema.json, public preset model
│   ├── http/
│   │   ├── presets.py                   CREATE  GET /api/presets (public, read-only)
│   │   └── admin/
│   │       ├── __init__.py              CREATE  the guarded router; includes the five below
│   │       ├── questions.py             CREATE  list · get · create · patch · deactivate
│   │       ├── categories.py            CREATE  list · create · patch
│   │       ├── media.py                 CREATE  POST /api/admin/media (streaming, own cap)
│   │       ├── imports.py               CREATE  dry-run · confirm · rejected.csv
│   │       ├── invites.py               CREATE  issue · list · revoke
│   │       ├── users.py                 CREATE  list · deactivate · role
│   │       └── presets.py               CREATE  CRUD · coverage
│   └── schemas/
│       └── admin/
│           ├── __init__.py              CREATE  re-exports for contracts.py
│           ├── questions.py             CREATE  QuestionDetail, QuestionSummary, page, writes
│           ├── media.py                 CREATE  MediaAssetSummary
│           ├── imports.py               CREATE  ImportReport, ImportRow, ImportSummary
│           ├── categories.py            CREATE  CategoryView, CreateCategoryRequest, Rename…
│           ├── invites.py               CREATE  IssueInvitesRequest, InviteView, IssuedInvite
│           ├── users.py                 CREATE  UserView, SetRoleRequest
│           └── presets.py               CREATE  PresetDetail, PresetWriteRequest, PresetCoverage
│       └── presets.py                   CREATE  PresetSummary, RulesView (public, rest.schema)
├── services/
│   ├── storage.py                       CREATE  MediaStore, ImportStagingStore, ObjectHead
│   └── admin.py                         CREATE  one Protocol + records per admin resource
├── storage/
│   ├── __init__.py                      CREATE
│   └── s3.py                            CREATE  S3MediaStore, S3ImportStagingStore (aioboto3)
├── media/
│   ├── __init__.py                      CREATE
│   ├── pipeline.py                      CREATE  ImageNormalizer, NormalizedImage, MediaRejected
│   └── gc.py                            CREATE  unreferenced-asset sweep (two-way check)
├── imports/
│   ├── __init__.py                      CREATE
│   ├── digest.py                        CREATE  prompt_digest, relocated out of db/ (Task 7)
│   ├── parse.py                         CREATE  pure csv/zip → ParsedImport (rows + rejections)
│   └── retire.py                        CREATE  the expiry state machine
├── db/
│   ├── models/content.py                MODIFY  nothing structural; docstring for status values
│   ├── repositories/
│   │   ├── question_admin.py            CREATE  QuestionAdminRepository (list/get/create/update)
│   │   ├── categories.py                CREATE  CategoryRepository
│   │   ├── media.py                      CREATE  MediaAssetRepository
│   │   ├── imports.py                   CREATE  QuestionImportRepository
│   │   ├── auth.py                      MODIFY  invite issue/list/revoke; user list/role/deactivate
│   │   └── presets.py                   MODIFY  list/create/update/deactivate/coverage
│   └── migrations/versions/
│       └── 0004_question_search.py      CREATE  pg_trgm + GIN index on lower(prompt)
├── config.py                            MODIFY  S3, media and import settings
└── cli.py                               MODIFY  `media-gc`

backend/
├── docker-compose.test.yml              MODIFY  garage-test service (no init service — see T2)
├── testing/garage.toml                  CREATE  single-node test config
├── testing/garage-init.sh               CREATE  host-side: layout · buckets · key · website
├── .env.example                         MODIFY  the new TRIVIADOR_S3_* keys
└── tests/
    ├── test_layering.py                 MODIFY  storage/, media/, imports/ gates
    ├── api/conftest.py                  MODIFY  admin_client fixture; fakes for new ports
    ├── api/fakes.py                     MODIFY  InMemoryMediaStore, InMemoryStagingStore, ...
    ├── api/test_admin_guard.py          CREATE
    ├── api/test_body_limit_exemption.py CREATE
    ├── api/test_presets.py              CREATE  the public preset list
    ├── api/test_admin_questions.py      CREATE
    ├── api/test_admin_categories.py     CREATE
    ├── api/test_admin_media.py          CREATE
    ├── api/test_admin_imports.py        CREATE
    ├── api/test_admin_invites.py        CREATE
    ├── api/test_admin_users.py          CREATE
    ├── api/test_admin_presets.py        CREATE
    ├── api/integration/test_admin_session.py   CREATE  the whole-admin path, real PG + Garage
    ├── media/test_pipeline.py           CREATE  pure, no I/O
    ├── imports/test_parse.py            CREATE  pure, no I/O
    ├── imports/test_retire.py           CREATE  the expiry machine, over fakes
    ├── storage/conftest.py              CREATE  Garage fixtures (integration)
    ├── storage/test_s3.py               CREATE
    ├── db/test_media_repository.py      CREATE
    ├── db/test_question_admin.py        CREATE  incl. the version-bump lock test
    ├── db/test_admin_repositories.py    CREATE  invites, users, presets, imports
    └── db/test_media_gc.py              CREATE  the two-way reference check

contracts/admin.schema.json              CREATE  (by `uv run triviador export-contracts`)
contracts/errors.json                    MODIFY  (regenerated: six new codes)
contracts/rest.schema.json               MODIFY  (regenerated: PresetSummary)
contracts/openapi.json                   MODIFY  (regenerated)
frontend/src/shared/api/generated/admin.ts   CREATE  (by `pnpm codegen`; unused until Plan 7B)
```

**Why `schemas/admin/` is a package and `http/admin/` is a package.** Plan 5 kept one file per REST resource and one schema module per resource; admin is six resources, and a single `admin.py` on either side would be the largest file in the codebase and the one every task in this plan edits at once. Splitting by resource is also what makes Task 13's contract export a list of imports rather than a merge.

---

## Design decisions this plan makes that the spec does not state

1. **`GET /api/presets` exists, and it is public to any signed-in user.** Spec 1B §6.1 lists preset routes only under `/api/admin`. Taken literally, an admin can author presets that no player can ever select: `POST /api/games` accepts `preset_id`, Plan 6's lobby sends `null`, and the only lever an admin has is which preset is *default*. One read-only route (active presets, id + name + rules) closes that, and 7B turns Plan 6's fixed `Default rules — presets are configurable from the admin screens.` line into a picker. **This is a deliberate deviation from the spec's route list.** It adds no write path and no new port method beyond a `list_active`.

2. **Six new `ApiErrorCode` values, not a reused `validation_failed`.** `media_rejected`, `import_not_confirmable`, `slug_taken`, `default_preset`, `last_admin`, `self_target`. Each is a case the admin UI must render differently — "your PNG is 6 MB" and "your form is missing a field" are not the same screen — and §6.3's envelope is the only channel that carries the distinction. The enum is the closed list codegen exports, so adding to it is the designed extension path (Plan 5, decision 9).

3. **Prompt search is `pg_trgm` + `ILIKE`, not `tsvector`.** Spec 1 §10.2 says "full-text search on `prompt`". PostgreSQL ships no Czech text-search configuration, the deployment's map is Czechia, and the seed bank is English — so a stemming configuration would have to be chosen wrongly for one of the two. A trigram index over `lower(prompt)` is language-independent, matches the substring an admin actually types, and costs one migration. It gives up ranking and stemming, which a bank of a few thousand rows does not miss.

4. **The two stores are separate ports with separate adapters, not one port with two prefixes.** §9.1's argument, restated because it is the one that must survive refactoring: the security boundary is the bucket. `triviador-media` is anonymously readable; `triviador-staging` holds raw uploads with unpublished answer keys. A prefix bug in the wrong direction publishes the answers.

5. **Media processing is `aioboto3` for I/O and `asyncio.to_thread` for pixels.** Storage is network-bound and awaits; WebP re-encoding is CPU-bound and must not run on the event loop at all. The semaphore of one lives on `ImageNormalizer`, an object built once in the composition root, rather than as a module-level global — a module-level `asyncio.Semaphore` is shared by every test in a session and is exactly the kind of state that makes a test suite order-dependent.

6. **`media-gc` is one command that retires imports first and deletes blobs second.** §9.3's expiry machine and §10.4's asset sweep are separate rules, but they are the same operational moment ("the rare, destructive cleanup an operator runs"), and running the sweep before the retirement would leave a staged object alive for exactly one more cycle every time. Its `--after-restore` flag is §9.3's post-restore rule.

8. **`POST /api/admin/questions/{id}/activate` exists, though §6.1 does not list it.** Spec 1 §10.2 puts `is_active` among the editor's common fields, so an admin must be able to set it both ways; Spec 1B §6.1 names only `deactivate`. A bank whose rows are never deleted (§7) and whose retirement is irreversible is a bank where one misclick permanently shrinks the question pool. It is a second route rather than a field on `PATCH` because `PATCH` always bumps `questions.version` and §7 requires that an activity toggle not bump it — two routes keep both rules true without a field comparison deciding which one applies.

9. **The media write paths verify their blob after the row commits, and `media-gc` deletes rows before objects.** §10.4 says an asset is collectable when nothing references it, and §10.3 orders blob-before-transaction; neither says what happens when a sweep and an upload overlap, and two interleavings genuinely lose data:

   - The sweep's orphan pass treats any object with no row as garbage — including one an upload wrote thirty milliseconds ago whose row has not committed yet.
   - An unreferenced asset can acquire its first reference between the sweep's check and its delete, leaving a valid foreign key pointing at a missing object.

   Three mechanics close them, and none of them holds a database transaction open across a network write. **Row-first deletion under `FOR UPDATE`**, with the reference check repeated inside that transaction: a concurrent `INSERT` into `questions` takes `FOR KEY SHARE` on the same `media_assets` row, so it cannot slip between the check and the delete. **A grace period** on the orphan pass (`media_gc_grace_minutes`, default 60): an object younger than that is presumed to belong to an upload in flight, and §10.3's failed-transaction orphans are not urgent. **Verify-after-commit** in both write paths: once the row is committed, the route `HEAD`s the blob and re-`PUT`s it if a sweep removed it in the window — one round trip on a LAN, and the only repair that needs no lock at all.

7. **The `question_imports.status` values are closed by this plan**: `validated`, `confirmed`, `expired`, `cleaned`. Plan 3 deliberately left the column unconstrained because the spec named them only in prose. This plan is where the state machine is implemented, so it is also where the values become a `CheckConstraint`-free but code-enforced closed set — `imports/retire.py` owns every transition, and no other module writes the column.

---

## Task 1: The guard, the mount, and the hole in the body limit

Nothing under `/api/admin` may be reachable by a player, and no future file added to `http/admin/` may be able to forget that. The guard therefore lives on the parent router, and this task also cuts the one hole the upload routes need in `BodyLimitMiddleware` — the middleware's own docstring already specifies this ("Plan 7's media upload … needs a streaming route of its own and must exclude itself from this middleware rather than raise the cap for everybody").

**Files:**
- Modify: `backend/src/triviador/api/deps.py` (after `current_principal`, ~line 193)
- Modify: `backend/src/triviador/api/middleware.py:113-145` (`BodyLimitMiddleware`)
- Modify: `backend/src/triviador/api/app.py:66-80` (middleware wiring, router includes)
- Create: `backend/src/triviador/api/http/admin/__init__.py`
- Modify: `backend/tests/api/conftest.py` (`_second_client` gains `role`; new `admin_client`)
- Test: `backend/tests/api/test_admin_guard.py`
- Test: `backend/tests/api/test_body_limit_exemption.py`

**Interfaces:**
- Consumes: `current_principal`, `Principal`, `ApiError`, `ApiErrorCode.FORBIDDEN` (all existing).
- Produces:
  - `triviador.api.deps.current_admin(principal) -> AuthenticatedPrincipal` and `AdminPrincipal = Annotated[AuthenticatedPrincipal, Depends(current_admin)]`
  - `triviador.api.http.admin.build_admin_router(*routers: APIRouter) -> APIRouter`
  - `triviador.api.http.admin.router: APIRouter` — the mounted instance
  - `triviador.api.http.admin.UPLOAD_PATHS: tuple[str, ...]` — the paths exempt from the global body limit
  - `BodyLimitMiddleware(app, *, max_bytes: int, exempt_paths: tuple[str, ...] = ())`
  - test fixture `admin_client` — an `httpx.AsyncClient` signed in as `admin` (role `admin`, token `"tok-admin"`)

- [ ] **Step 1: Write the failing guard test**

Create `backend/tests/api/test_admin_guard.py`:

```python
"""The guard is a property of the router, not of any route.

A per-route `Depends(current_admin)` is one `git` conflict away from being
dropped from a single handler, and the failure is silent — the route keeps
working, for everybody. `test_every_admin_route_is_guarded` walks the real
app instead of trusting the source: it is vacuous while `http/admin/` holds
no routes, and it covers Task 4's first one automatically.
"""

import httpx
import pytest
from fastapi import APIRouter
from fastapi.routing import APIRoute

from tests.api.conftest import ORIGIN
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


def test_the_walk_sees_a_route_mounted_the_way_every_admin_route_will_be(
    deps: AppDependencies,
) -> None:
    """The topology every later task uses: a prefix-less sub-router,
    included into `build_admin_router`, included into the app.

    Its raw `APIRoute.path` is `/probe` — the `/api/admin` half lives on the
    include context — and its guard is on that include context too, not in
    its `dependant`. A walk that gets either wrong reports this route as
    absent or as unguarded, and both failures are silent.
    """
    app = create_app(deps)
    app.include_router(build_admin_router(probe))
    assert "/api/admin/probe" in {mounted.path for mounted in api_routes(app)}
    assert unguarded_admin_routes(app) == []


def test_a_sub_router_mounted_without_the_guard_is_caught(deps: AppDependencies) -> None:
    """The same topology, minus the guard: a bare `APIRouter(prefix=...)`
    used in place of `build_admin_router`. This is the mistake the check
    exists for, and it is not the same mistake as the rogue route below —
    that one bypasses the wrapper, this one builds the wrapper wrongly.
    """
    unguarded_wrapper = APIRouter(prefix="/api/admin")
    unguarded_wrapper.include_router(probe)
    app = create_app(deps)
    app.include_router(unguarded_wrapper)
    assert unguarded_admin_routes(app) == ["/api/admin/probe"]


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
```

...with `from fastapi import APIRouter, FastAPI` at the top of the module, and `api_routes`
imported from `tests.api.conftest`. `NamedTuple` comes from `typing`.

Add `api_routes` to `backend/tests/api/conftest.py`, beside the other shared helpers:

```python
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
```

Add the `_seed_admin` helper and the `admin_client` fixture to `backend/tests/api/conftest.py`, and give `_second_client` a role:

```python
async def _second_client(
    deps: AppDependencies,
    settings: Settings,
    *,
    user_id: str,
    token: str,
    role: UserRole = UserRole.PLAYER,
) -> AsyncIterator[httpx.AsyncClient]:
```

...passing `role=role` to `deps.users.create(...)`, and then:

```python
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
```

`test_admin_guard.py` imports `_seed_admin` from the conftest (`from tests.api.conftest import ORIGIN, _seed_admin`).

- [ ] **Step 2: Run it and watch it fail**

Run: `cd backend && uv run pytest tests/api/test_admin_guard.py -v`
Expected: FAIL — `ImportError: cannot import name 'current_admin' from 'triviador.api.deps'`.

- [ ] **Step 3: Add the guard**

Append to `backend/src/triviador/api/deps.py`:

```python
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
```

...with `UserRole` added to the existing `from triviador.services.identity import (...)`.

Create `backend/src/triviador/api/http/admin/__init__.py`:

```python
"""§6.1's admin surface: six resources, one guard, one prefix.

The guard is declared on this router rather than on each route below it.
That is the whole reason this package has an `__init__.py` with code in
it: a new module under `http/admin/` inherits the guard by being included,
and "forgot the dependency" stops being a thing that can happen.
`tests/api/test_admin_guard.py` walks the built app and fails on any
`/api/admin` route whose dependency tree does not contain `current_admin`.
"""

from fastapi import APIRouter, Depends

from triviador.api.deps import current_admin

# The two routes that take a body larger than `max_body_bytes`, and so opt
# out of `BodyLimitMiddleware`'s buffering. Each imposes its own cap while
# reading — `media_max_bytes` and `import_max_bytes` respectively. Exempt
# *paths*, not "anything under /api/admin": an exemption is a hole, and a
# hole the width of a whole router is one nobody would notice widening.
UPLOAD_PATHS = ("/api/admin/media", "/api/admin/questions/import/dry-run")


def build_admin_router(*routers: APIRouter) -> APIRouter:
    router = APIRouter(prefix="/api/admin", tags=["admin"], dependencies=[Depends(current_admin)])
    for sub in routers:
        router.include_router(sub)
    return router


# Sub-routers are added to this call as the tasks that create them land.
router = build_admin_router()
```

Mount it in `backend/src/triviador/api/app.py` — add `from triviador.api.http import admin, auth, games, health, maps` and, after `app.include_router(games.router)`:

```python
    app.include_router(admin.router)
```

- [ ] **Step 4: Run the guard test**

Run: `cd backend && uv run pytest tests/api/test_admin_guard.py -v`
Expected: PASS (4 tests; `test_every_admin_route_is_guarded` passes vacuously).

- [ ] **Step 5: Write the failing body-limit exemption test**

Create `backend/tests/api/test_body_limit_exemption.py`:

```python
"""An exempt path is not "unlimited" — it is "bounded by the route".

The middleware buffers whole bodies (see its docstring); a 32 MiB import
would be held in memory twice and refused at 1 MiB. So the two upload
paths opt out, and the routes that own them cap themselves. This module
tests the middleware half; `test_admin_media.py` and
`test_admin_imports.py` test that the routes really do cap.
"""

import httpx
import pytest
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from triviador.api.middleware import BodyLimitMiddleware


async def _echo_size(request: Request) -> JSONResponse:
    body = await request.body()
    return JSONResponse({"received": len(body)})


def _app(exempt: tuple[str, ...]) -> Starlette:
    app = Starlette(routes=[Route("/open", _echo_size, methods=["POST"]),
                            Route("/capped", _echo_size, methods=["POST"])])
    app.add_middleware(BodyLimitMiddleware, max_bytes=16, exempt_paths=exempt)
    return app


async def _post(app: Starlette, path: str, size: int) -> httpx.Response:
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        return await client.post(path, content=b"x" * size)


@pytest.mark.parametrize("size", [17, 4096])
async def test_a_non_exempt_path_is_still_refused(size: int) -> None:
    response = await _post(_app(("/open",)), "/capped", size)
    assert response.status_code == 413
    assert response.json()["code"] == "payload_too_large"


async def test_an_exempt_path_receives_the_whole_body(size: int = 4096) -> None:
    response = await _post(_app(("/open",)), "/open", size)
    assert response.status_code == 200
    assert response.json() == {"received": size}


async def test_exemption_is_a_prefix_match_on_the_path_only() -> None:
    """`/openish` must not inherit `/open`'s exemption by accident — the
    match is on the full path, not on `startswith`."""
    app = Starlette(routes=[Route("/openish", _echo_size, methods=["POST"])])
    app.add_middleware(BodyLimitMiddleware, max_bytes=16, exempt_paths=("/open",))
    response = await _post(app, "/openish", 4096)
    assert response.status_code == 413
```

- [ ] **Step 6: Run it and watch it fail**

Run: `cd backend && uv run pytest tests/api/test_body_limit_exemption.py -v`
Expected: FAIL — `TypeError: BodyLimitMiddleware.__init__() got an unexpected keyword argument 'exempt_paths'`.

- [ ] **Step 7: Cut the hole**

In `backend/src/triviador/api/middleware.py`, change `BodyLimitMiddleware.__init__` and the top of `__call__`:

```python
    def __init__(
        self, app: ASGIApp, *, max_bytes: int, exempt_paths: tuple[str, ...] = ()
    ) -> None:
        self.app = app
        self.max_bytes = max_bytes
        self.exempt_paths = exempt_paths

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        # Equality, not `startswith`: a prefix match would extend the
        # exemption to every path that merely begins with an exempt one,
        # and the exemption is the security-relevant half of this class.
        if scope["path"] in self.exempt_paths:
            await self.app(scope, receive, send)
            return
```

Extend the class docstring with one paragraph:

```
    An exempt path is not unbounded — it is bounded by its own route,
    which reads the stream itself and stops at its own cap
    (`media_max_bytes`, `import_max_bytes`). The exemption exists because
    buffering a 32 MiB import here would hold it twice and refuse it at
    1 MiB; it is a list of exact paths (`http/admin.UPLOAD_PATHS`), never
    a prefix, so widening it is a deliberate edit.
```

Wire it in `backend/src/triviador/api/app.py`:

```python
    app.add_middleware(
        BodyLimitMiddleware,
        max_bytes=deps.settings.max_body_bytes,
        exempt_paths=admin.UPLOAD_PATHS,
    )
```

- [ ] **Step 8: Run both test modules and the full fast lane**

Run: `cd backend && uv run pytest tests/api -m "not integration" -q && uv run mypy && uv run ruff check .`
Expected: PASS, no type errors, no lint errors.

- [ ] **Step 9: Commit**

```bash
git add backend/src/triviador/api backend/tests/api
git commit -m "feat(admin): guard the admin router and exempt the upload paths from the body limit"
```

---

## Task 2: Two ports, two buckets, and a Garage the tests can actually talk to

**Files:**
- Create: `backend/src/triviador/services/storage.py`
- Create: `backend/src/triviador/storage/__init__.py`, `backend/src/triviador/storage/s3.py`
- Create: `backend/testing/garage.toml`, `backend/testing/garage-init.sh`
- Modify: `backend/docker-compose.test.yml`
- Modify: `backend/src/triviador/config.py`, `backend/.env.example`
- Modify: `backend/pyproject.toml` (add `aioboto3`)
- Modify: `backend/tests/test_layering.py`
- Modify: `backend/tests/api/test_settings.py` (the new startup assertion)
- Test: `backend/tests/storage/__init__.py`, `backend/tests/storage/conftest.py`, `backend/tests/storage/test_s3.py`

**Interfaces:**
- Produces:
  - `triviador.services.storage.MediaStore` — `put(key, data, *, content_type, cache_control) -> None`, `open(key) -> bytes | None`, `head(key) -> ObjectHead | None`, `delete(key) -> None`, `list_objects(*, prefix) -> tuple[StoredObject, ...]`
  - `triviador.services.storage.ObjectHead(byte_size, content_type, cache_control, last_modified)` and `StoredObject(key, byte_size, last_modified)`
  - `triviador.services.storage.ImportStagingStore` — `put(key, data, *, content_type) -> None`, `open(key) -> bytes | None`, `delete(key) -> None`
  - `triviador.storage.s3.S3MediaStore(...)`, `triviador.storage.s3.S3ImportStagingStore(...)`
  - `Settings.s3_endpoint_url`, `.s3_region`, `.s3_access_key_id`, `.s3_secret_access_key`, `.media_bucket`, `.staging_bucket`, `.media_max_bytes`, `.media_max_pixels`, `.media_target_px`, `.import_max_bytes`, `.import_ttl_hours`
  - test fixtures `media_store`, `staging_store` (session-scoped, real Garage)

- [ ] **Step 1: The Garage CLI, already verified against the pinned image**

Spec 1B §13 open item 4 asks that this be verified rather than assumed. It has been, against
`dxflrs/garage:v1.1.0`, and the results are below — **this step is a re-confirmation, not a
discovery exercise**. Run each command and check the output matches; if any of them disagrees,
stop and report, because the rest of this task is built on them.

```bash
docker run --rm --entrypoint /garage dxflrs/garage:v1.1.0 --version
docker run --rm --entrypoint /garage dxflrs/garage:v1.1.0 layout assign --help
docker run --rm --entrypoint /garage dxflrs/garage:v1.1.0 layout apply --help
docker run --rm --entrypoint /garage dxflrs/garage:v1.1.0 key import --help
docker run --rm --entrypoint /garage dxflrs/garage:v1.1.0 bucket allow --help
docker run --rm --entrypoint /garage dxflrs/garage:v1.1.0 bucket website --help
```

What they establish, and what each one changes about the naive version of this task:

- **The binary is `/garage`, and it is not the image's entrypoint.** `docker run <image> layout …`
  fails with "executable file not found"; every invocation needs `--entrypoint /garage` (or
  `docker exec <container> /garage …`).
- **The image has no shell at all** — `/bin/sh` does not exist. So the init cannot be a compose
  service running a script inside the container. See Step 2: the script runs on the *host* and
  drives the container with `docker compose exec`.
- `layout assign -z <zone> -c <capacity> <node-id>`, where capacity suffixes are `B, KB, MB, GB,
  TB, PB` — **`1G` is not a valid suffix; write `1GB`**.
- `layout apply --version <N>` — fails unless N is exactly one more than the current version.
- `key import [--yes] [-n <name>] <key-id> <secret-key>` — positional id and secret, `--yes`
  required for a non-interactive run.
- `bucket allow [--read] [--write] [--owner] <bucket> --key <key-pattern>` — the bucket is
  positional, the key is a named option.
- `bucket website --allow <bucket>`.
- The config key for a single-node cluster is `replication_factor = 1` (v1.x spelling).

- [ ] **Step 2: Write the Garage service, its config, and its init script**

Create `backend/testing/garage.toml`:

```toml
# Test-only Garage. Single node, replication factor 1, everything in tmpfs:
# nothing here should survive a run, for the same reason the test database
# uses tmpfs — a store with leftover state is a test that lies.
#
# `rpc_secret` and the admin token are fixed, published, and worthless:
# this node listens on 127.0.0.1 only and holds nothing but test fixtures.
metadata_dir = "/var/lib/garage/meta"
data_dir = "/var/lib/garage/data"
db_engine = "sqlite"

replication_factor = 1

rpc_bind_addr = "[::]:3901"
rpc_public_addr = "127.0.0.1:3901"
rpc_secret = "0000000000000000000000000000000000000000000000000000000000000001"

[s3_api]
s3_region = "garage"
api_bind_addr = "[::]:3900"
root_domain = ".s3.garage.localhost"

[s3_web]
bind_addr = "[::]:3902"
root_domain = ".web.garage.localhost"
index = "index.html"

[admin]
api_bind_addr = "[::]:3903"
admin_token = "test-admin-token"
```

Create `backend/testing/garage-init.sh` (mode 0755). **It runs on the host, not in the
container**: the Garage image ships no shell, so there is nothing inside it that could execute a
script. It drives the running container with `docker compose exec` instead — one process per
command, each one the `/garage` binary directly.

```sh
#!/usr/bin/env bash
# Initialise the test Garage: layout, buckets, key, website.
#
# Runs on the host, because `dxflrs/garage:v1.1.0` contains no shell —
# `/bin/sh` does not exist in that image, so an init *service* running a
# script inside it is not possible. Every command here is instead
# `docker compose exec` of the `/garage` binary, which needs no shell.
#
# Idempotent throughout: the harness has no way to know whether a previous
# `docker compose up` already initialised this node, and re-running must be
# free. Verified against dxflrs/garage:v1.1.0 (see Task 2 Step 1) — every
# flag below was checked against that image's `--help`.
#
# Usage, after `docker compose -f docker-compose.test.yml up -d`:
#     ./testing/garage-init.sh
set -euo pipefail

COMPOSE=(docker compose -f "$(dirname "$0")/../docker-compose.test.yml")
KEY_ID="${TEST_S3_KEY_ID:-GK11111111111111111111111111}"
KEY_SECRET="${TEST_S3_KEY_SECRET:-2222222222222222222222222222222222222222222222222222222222222222}"

garage() { "${COMPOSE[@]}" exec -T garage-test /garage "$@"; }

# Wait for the daemon: `up -d` returns as soon as the container starts, and
# the first `garage` call can beat the RPC listener by a second or two.
for _ in $(seq 1 30); do
  if garage status >/dev/null 2>&1; then break; fi
  sleep 1
done

# One node, one zone, 1 GB of nominal capacity ("1G" is not a valid suffix —
# the accepted set is B, KB, MB, GB, TB, PB). `layout apply` fails once the
# layout is already at that version, which is what `|| true` absorbs on a
# re-run.
NODE_ID="$(garage node id -q | cut -d@ -f1 | tr -d '\r')"
garage layout assign -z dc1 -c 1GB "$NODE_ID" || true
garage layout apply --version 1 || true

for bucket in triviador-media triviador-staging; do
  garage bucket create "$bucket" || true
done

garage key import --yes -n test "$KEY_ID" "$KEY_SECRET" || true

for bucket in triviador-media triviador-staging; do
  garage bucket allow --read --write --owner "$bucket" --key test
done

# Website-enabled, anonymous read — §9.1's media bucket, and only it. If
# this line ever names the staging bucket, raw import uploads (answer keys
# included) become anonymously readable.
garage bucket website --allow triviador-media

garage bucket info triviador-media
garage bucket info triviador-staging
```

Append to `backend/docker-compose.test.yml`:

```yaml
  # Test-only Garage, §9.1's two buckets. Same reasoning as postgres-test:
  # a fixed loopback port that cannot collide with anything real, and no
  # persistence. The S3 API is on 3900; `testing/garage-init.sh` runs once
  # against it from the host.
  garage-test:
    image: dxflrs/garage:v1.1.0
    environment:
      GARAGE_ALLOW_WORLD_READABLE_SECRETS: "true"
    volumes:
      - ./testing/garage.toml:/etc/garage.toml:ro
    ports:
      - "127.0.0.1:3900:3900"
      - "127.0.0.1:3903:3903"
    tmpfs:
      - /var/lib/garage/meta
      - /var/lib/garage/data
    healthcheck:
      test: ["CMD", "/garage", "status"]
      interval: 2s
      timeout: 3s
      retries: 30

```

There is deliberately **no `garage-init` service**: the image has no shell to run one with, and a
service per CLI command would be six services to express one script. Initialisation is the host
script above, run once after the containers come up — the same shape the Postgres container
already has, where the developer brings the compose file up before running the suite.

Bring it up and initialise it:

```bash
cd backend
docker compose -f docker-compose.test.yml up -d
chmod +x testing/garage-init.sh
./testing/garage-init.sh
```

Expected: the two `garage bucket info` blocks at the end of the output, each naming the `test` key
with read/write/owner, and `triviador-media` showing a website configuration.

- [ ] **Step 3: Write the failing store test**

Create `backend/tests/storage/__init__.py` (empty) and `backend/tests/storage/conftest.py`:

```python
"""Fixtures for the object-store suite: a real Garage, per §9.1's two buckets.

Not MinIO. Production runs Garage (Spec 1B §10.3), and the behaviours this
suite pins — a 404 on a missing key, an idempotent delete, `Cache-Control`
surviving a round trip as object metadata — are exactly the ones an
S3-compatible stand-in is entitled to get subtly right in a different way.

Keys are namespaced per test with a `uuid4` prefix rather than cleaned up
between tests: the store is content-addressed in production and the bucket
is tmpfs here, so collision-avoidance is worth more than tidiness.
"""

import os
import uuid
from collections.abc import AsyncIterator

import pytest
import pytest_asyncio

from triviador.storage.s3 import S3ImportStagingStore, S3MediaStore

ENDPOINT = os.environ.get("TRIVIADOR_TEST_S3_ENDPOINT", "http://127.0.0.1:3900")
KEY_ID = os.environ.get("TRIVIADOR_TEST_S3_KEY_ID", "GK11111111111111111111111111")
KEY_SECRET = os.environ.get("TRIVIADOR_TEST_S3_KEY_SECRET", "2" * 64)

pytestmark = pytest.mark.integration


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    """Same gate as `tests/db/conftest.py`: a module here without the mark
    would be deselected by `-m "not integration"` and still require Garage."""
    for item in items:
        if "integration" not in item.keywords:
            raise pytest.UsageError(f"{item.nodeid}: tests/storage requires the integration mark")


@pytest_asyncio.fixture
async def prefix() -> str:
    return f"t-{uuid.uuid4().hex}"


@pytest_asyncio.fixture
async def media_store() -> AsyncIterator[S3MediaStore]:
    yield S3MediaStore(
        endpoint_url=ENDPOINT,
        region="garage",
        access_key_id=KEY_ID,
        secret_access_key=KEY_SECRET,
        bucket="triviador-media",
    )


@pytest_asyncio.fixture
async def staging_store() -> AsyncIterator[S3ImportStagingStore]:
    yield S3ImportStagingStore(
        endpoint_url=ENDPOINT,
        region="garage",
        access_key_id=KEY_ID,
        secret_access_key=KEY_SECRET,
        bucket="triviador-staging",
    )
```

Create `backend/tests/storage/test_s3.py`:

```python
import pytest

from triviador.storage.s3 import S3ImportStagingStore, S3MediaStore

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


async def test_put_then_open_returns_the_bytes(media_store: S3MediaStore, prefix: str) -> None:
    await media_store.put(
        f"{prefix}/a.webp", b"payload", content_type="image/webp", cache_control="immutable"
    )
    assert await media_store.open(f"{prefix}/a.webp") == b"payload"


async def test_open_of_a_missing_key_is_none_not_an_exception(
    media_store: S3MediaStore, prefix: str
) -> None:
    """`None`, because "no such asset" is an ordinary answer on the
    `media-gc` and media-serving paths, and a caller that has to catch
    `ClientError` to learn it is a caller that eventually catches a
    credentials failure by mistake."""
    assert await media_store.open(f"{prefix}/absent.webp") is None


async def test_delete_is_idempotent(media_store: S3MediaStore, prefix: str) -> None:
    """`media-gc` deletes the object and then updates the row; a crash
    between the two makes the next run repeat the delete. If that raised,
    the sweep could never finish."""
    await media_store.put(f"{prefix}/b.webp", b"x", content_type="image/webp")
    await media_store.delete(f"{prefix}/b.webp")
    await media_store.delete(f"{prefix}/b.webp")
    assert await media_store.open(f"{prefix}/b.webp") is None


async def test_cache_control_is_stored_on_the_object(
    media_store: S3MediaStore, prefix: str
) -> None:
    """§9.2: the header is object metadata set at PUT time, so Garage
    returns it on a 200 and — correctly — not on a 404. A proxy-level
    header would attach a one-year lifetime to error responses."""
    await media_store.put(
        f"{prefix}/c.webp",
        b"x",
        content_type="image/webp",
        cache_control="public, max-age=31536000, immutable",
    )
    head = await media_store.head(f"{prefix}/c.webp")
    assert head is not None
    assert head.cache_control == "public, max-age=31536000, immutable"
    assert head.content_type == "image/webp"


async def test_list_objects_paginates_past_one_thousand(
    media_store: S3MediaStore, prefix: str
) -> None:
    """S3 truncates a listing at 1000 keys. `media-gc` compares the store
    against the database, so a listing that silently stops at 1000 would
    make every asset past the first thousand invisible — and therefore
    never collected."""
    for i in range(1002):
        await media_store.put(f"{prefix}/{i}.webp", b"x", content_type="image/webp")
    listed = await media_store.list_objects(prefix=prefix)
    assert len(listed) == 1002


async def test_a_listing_carries_the_age_the_grace_period_needs(
    media_store: S3MediaStore, prefix: str
) -> None:
    """`media-gc` skips objects younger than `media_gc_grace_minutes`,
    which it can only do if the listing says how old they are."""
    await media_store.put(f"{prefix}/fresh.webp", b"x", content_type="image/webp")
    listed = await media_store.list_objects(prefix=f"{prefix}/fresh")
    assert listed[0].last_modified.tzinfo is not None


async def test_the_staging_store_writes_to_a_different_bucket(
    staging_store: S3ImportStagingStore, media_store: S3MediaStore, prefix: str
) -> None:
    """The security boundary of §9.1, asserted rather than assumed: a key
    written to staging is not readable from the public media bucket."""
    await staging_store.put(f"{prefix}/raw.zip", b"secret", content_type="application/zip")
    assert await staging_store.open(f"{prefix}/raw.zip") == b"secret"
    assert await media_store.open(f"{prefix}/raw.zip") is None
```

- [ ] **Step 4: Run it and watch it fail**

Run: `cd backend && uv run pytest tests/storage -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'triviador.storage'`.

- [ ] **Step 5: Declare the ports**

Create `backend/src/triviador/services/storage.py`:

```python
"""Two object stores, because §9.1 makes them two buckets.

`MediaStore` is website-enabled and anonymously readable; every object in
it is a normalized WebP whose key is its own content hash.
`ImportStagingStore` is private, holds the raw bytes an admin uploaded —
answer keys included — and expires by lifecycle.

They are declared as two Protocols rather than one store plus a prefix
convention for the reason §9.1 states: the security boundary is the
bucket, and a prefix bug in the wrong direction publishes unvalidated
uploads. Structurally `MediaStore` is a superset (`head`, `list_objects`), so the
type system alone will not stop a caller from passing the wrong one — the
composition root is where they are told apart, and
`tests/api/test_admin_wiring.py` asserts the two adapters carry different
bucket names.
"""

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class ObjectHead:
    """What a `HEAD` answers, and nothing more. `media-gc` needs ages;
    the upload path needs to know the object is still there; nobody needs
    the body."""

    byte_size: int
    content_type: str
    cache_control: str | None
    last_modified: datetime


@dataclass(frozen=True)
class StoredObject:
    """One entry of a listing.

    `last_modified` is part of it because `media-gc`'s orphan pass is
    age-aware: an object with no database row is either garbage from a
    failed transaction (§10.3) or an upload whose row has not committed
    yet, and only its age tells the two apart.
    """

    key: str
    byte_size: int
    last_modified: datetime


class ImportStagingStore(Protocol):
    async def put(self, key: str, data: bytes, *, content_type: str) -> None: ...

    async def open(self, key: str) -> bytes | None:
        """`None` for a missing key, never an exception: "the staged object
        is gone" is an ordinary state of §9.3's expiry machine, reached by
        every confirmed import and every restore."""
        ...

    async def delete(self, key: str) -> None:
        """Idempotent. §9.3 deletes the object and then updates the row, so
        a crash between the two means the next sweep repeats the delete."""
        ...


class MediaStore(Protocol):
    async def put(
        self, key: str, data: bytes, *, content_type: str, cache_control: str | None = None
    ) -> None: ...
    async def open(self, key: str) -> bytes | None: ...
    async def head(self, key: str) -> ObjectHead | None: ...
    async def delete(self, key: str) -> None: ...

    async def list_objects(self, *, prefix: str = "") -> tuple[StoredObject, ...]:
        """Every object, paginated to exhaustion. `media-gc` compares this
        listing against the database; a truncated one under-reports and
        leaves orphans uncollected forever."""
        ...
```

- [ ] **Step 6: Write the adapters**

Add the dependency: `cd backend && uv add aioboto3`.

Create `backend/src/triviador/storage/__init__.py`:

```python
"""S3 adapters. Implementations only — the ports are in `services/storage.py`.

This package sits where `maps/` sits: a concrete adapter with no port of
its own to hide behind, one layer below `api/` and beside `db/`.
`tests/test_layering.py` holds it to naming neither `api` nor `db`.
"""
```

Create `backend/src/triviador/storage/s3.py`:

```python
"""One client factory, two thin stores over it.

**Path addressing, always.** Garage serves buckets at
`http://host:3900/<bucket>/<key>`; virtual-host addressing would resolve
`triviador-media.<host>`, which does not exist on a LAN and fails as a DNS
error rather than as anything an operator can read.

**A client per call.** `aioboto3`'s client is an async context manager
holding a connection pool, and holding one open for the process lifetime
means owning its lifecycle across the app's own startup and shutdown for
no gain: admin traffic is a handful of requests from one or two people
(§1.1), and `aiohttp`'s connector setup is microseconds against a LAN
round trip. If that ever stops being true, the fix is one shared client
opened in `lifespan`, not a cache keyed on nothing.
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import aioboto3
from botocore.config import Config as BotoConfig
from botocore.exceptions import ClientError

from triviador.services.storage import ObjectHead

_MISSING = {"404", "NoSuchKey", "NotFound"}


class _S3Base:
    def __init__(
        self,
        *,
        endpoint_url: str,
        region: str,
        access_key_id: str,
        secret_access_key: str,
        bucket: str,
    ) -> None:
        self._session = aioboto3.Session()
        self._bucket = bucket
        self._client_kwargs: dict[str, Any] = {
            "endpoint_url": endpoint_url,
            "region_name": region,
            "aws_access_key_id": access_key_id,
            "aws_secret_access_key": secret_access_key,
            "config": BotoConfig(
                signature_version="s3v4",
                s3={"addressing_style": "path"},
                retries={"max_attempts": 3, "mode": "standard"},
            ),
        }

    @property
    def bucket(self) -> str:
        """Read by the wiring test, which asserts the two stores differ."""
        return self._bucket

    @asynccontextmanager
    async def _client(self) -> AsyncIterator[Any]:
        async with self._session.client("s3", **self._client_kwargs) as client:
            yield client

    async def _put(self, key: str, data: bytes, extra: dict[str, Any]) -> None:
        async with self._client() as client:
            await client.put_object(Bucket=self._bucket, Key=key, Body=data, **extra)

    async def open(self, key: str) -> bytes | None:
        async with self._client() as client:
            try:
                response = await client.get_object(Bucket=self._bucket, Key=key)
            except ClientError as exc:
                if exc.response.get("Error", {}).get("Code") in _MISSING:
                    return None
                raise
            body: bytes = await response["Body"].read()
            return body

    async def delete(self, key: str) -> None:
        # S3 `DeleteObject` is already idempotent — deleting an absent key
        # is a 204 — so this needs no `try`. Asserted by
        # `test_delete_is_idempotent` rather than assumed, because it is
        # the property §9.3's retryable state machine rests on.
        async with self._client() as client:
            await client.delete_object(Bucket=self._bucket, Key=key)


class S3ImportStagingStore(_S3Base):
    """Implements `services.storage.ImportStagingStore`."""

    async def put(self, key: str, data: bytes, *, content_type: str) -> None:
        await self._put(key, data, {"ContentType": content_type})


class S3MediaStore(_S3Base):
    """Implements `services.storage.MediaStore`."""

    async def put(
        self, key: str, data: bytes, *, content_type: str, cache_control: str | None = None
    ) -> None:
        extra: dict[str, Any] = {"ContentType": content_type}
        if cache_control is not None:
            # §9.2: set at PUT time as object metadata, so a 404 does not
            # inherit a one-year cache lifetime the way a blanket proxy
            # header would give it one.
            extra["CacheControl"] = cache_control
        await self._put(key, data, extra)

    async def head(self, key: str) -> ObjectHead | None:
        async with self._client() as client:
            try:
                response = await client.head_object(Bucket=self._bucket, Key=key)
            except ClientError as exc:
                if exc.response.get("Error", {}).get("Code") in _MISSING:
                    return None
                raise
        return ObjectHead(
            byte_size=int(response["ContentLength"]),
            content_type=str(response.get("ContentType", "")),
            cache_control=response.get("CacheControl"),
            last_modified=response["LastModified"],
        )

    async def list_objects(self, *, prefix: str = "") -> tuple[StoredObject, ...]:
        objects: list[StoredObject] = []
        async with self._client() as client:
            paginator = client.get_paginator("list_objects_v2")
            async for page in paginator.paginate(Bucket=self._bucket, Prefix=prefix):
                objects.extend(
                    StoredObject(
                        key=item["Key"],
                        byte_size=int(item["Size"]),
                        # botocore parses this into an aware datetime; the
                        # grace period compares it against `clock.now()`,
                        # which is also aware (§8.6 has no naive datetimes
                        # anywhere in this system).
                        last_modified=item["LastModified"],
                    )
                    for item in page.get("Contents", ())
                )
        return tuple(objects)
```

- [ ] **Step 7: Run the store test against real Garage**

Run: `cd backend && uv run pytest tests/storage -q`
Expected: PASS (7 tests). If `test_list_objects_paginates_past_one_thousand` is slow (it writes 1002 objects), that is expected — it is the only test here that costs seconds, and it is the one that catches a truncation bug nothing else would.

- [ ] **Step 8: Add the settings, and make an unconfigured deploy fail loudly**

In `backend/src/triviador/config.py`, add to `Settings` (after `media_public_base`):

```python
    # --- Object storage (Spec 1B §9.1, §10.3) -----------------------------
    s3_endpoint_url: str = "http://garage:3900"
    s3_region: str = "garage"
    s3_access_key_id: str = ""
    s3_secret_access_key: SecretStr = SecretStr("")
    media_bucket: str = "triviador-media"
    staging_bucket: str = "triviador-staging"

    # --- Media and import limits (Spec 1 §10.3, §10.4) --------------------
    # 5 MB and 4000 px are §10.4's stated validation bounds; 1280 px is its
    # re-encode target. `import_max_bytes` has no spec value: 32 MiB is
    # ~200 photographs at 150 KB, which is §10.3's stated bulk-import size.
    media_max_bytes: int = 5_242_880
    media_max_pixels: int = 4000
    media_target_px: int = 1280
    import_max_bytes: int = 33_554_432
    import_ttl_hours: int = 24
    # `media-gc` leaves an object younger than this alone: with no
    # database row it is indistinguishable from an upload whose row has
    # not committed yet (Decision 9), and §10.3's failed-transaction
    # orphans are never urgent.
    media_gc_grace_minutes: int = 60
```

...with `from pydantic import SecretStr, field_validator` at the top. Then extend `startup_problems`:

```python
    if not settings.s3_access_key_id or not settings.s3_secret_access_key.get_secret_value():
        problems.append(
            "S3_ACCESS_KEY_ID/S3_SECRET_ACCESS_KEY are empty: no media could be stored or read"
        )
```

...and make the placeholder scan see secrets, which `model_dump()` renders as `**********`:

```python
    for name, value in settings.model_dump().items():
        text = value.get_secret_value() if isinstance(value, SecretStr) else value
        if isinstance(text, str) and PLACEHOLDER in text:
            problems.append(f"{name} still holds its .env.example placeholder")
```

Add to `backend/.env.example`:

```
TRIVIADOR_S3_ENDPOINT_URL=http://garage:3900
TRIVIADOR_S3_REGION=garage
TRIVIADOR_S3_ACCESS_KEY_ID=CHANGE_ME
TRIVIADOR_S3_SECRET_ACCESS_KEY=CHANGE_ME
TRIVIADOR_MEDIA_BUCKET=triviador-media
TRIVIADOR_STAGING_BUCKET=triviador-staging
TRIVIADOR_IMPORT_TTL_HOURS=24
```

Update `backend/tests/api/test_settings.py`: every `startup_problems` case that expects "no problems" now needs S3 credentials set, and add one test asserting the new problem is reported when they are absent, plus one asserting a `SecretStr` holding `CHANGE_ME` is caught (the regression the `model_dump` change exists for).

**And update the existing integration fixture in the same step.** `backend/tests/api/integration/conftest.py`'s `client` fixture calls `build_app(settings)`, and `build_app` raises `RuntimeError` on any `startup_problems` entry — so the moment this assertion lands, every test in that directory fails on a configuration error rather than on anything it is testing:

```python
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
```

...importing `ENDPOINT`, `KEY_ID` and `KEY_SECRET` from `tests.storage.conftest` and `SecretStr` from pydantic. Verify with `uv run pytest tests/api/integration -q` **before** moving on — this is the one change in Task 2 that can silently break a suite two directories away.

- [ ] **Step 9: Extend the layering gate**

In `backend/tests/test_layering.py`, add:

```python
STORAGE = SRC / "triviador" / "storage"
MEDIA = SRC / "triviador" / "media"
IMPORTS = SRC / "triviador" / "imports"


@pytest.mark.parametrize("package", [STORAGE, MEDIA, IMPORTS])
def test_the_adapter_packages_do_not_import_the_layers_above_them(package: Path) -> None:
    """`storage/`, `media/` and `imports/` sit where `maps/` sits: concrete
    adapters, below `api/` and beside `db/`. Naming `api` would let the
    composition root's shape leak into a pixel encoder; naming `db` would
    put a session inside one, which is how a 200-image import ends up
    holding a transaction open for the length of a CPU-bound encode.

    `media/gc.py` is the one place that legitimately reads the event store,
    and it does so through a repository handed to it — never by importing
    `db` itself. That is why `db` is on this list rather than excused.
    """
    forbidden = ("triviador.api", "triviador.runtime", "triviador.db", "fastapi", "starlette")
    violations = [
        f"{path.relative_to(SRC)}: {module}"
        for path in sorted(package.rglob("*.py"))
        for module in sorted(_imported_modules(path))
        if _is_forbidden(module, forbidden)
    ]
    assert violations == [], violations
```

- [ ] **Step 10: Run everything and commit**

Run: `cd backend && uv run pytest -q && uv run mypy && uv run ruff check .`
Expected: PASS. (`pytest` here includes the integration lane; both containers must be up.)

```bash
git add backend/src/triviador/services/storage.py backend/src/triviador/storage \
        backend/testing backend/docker-compose.test.yml backend/src/triviador/config.py \
        backend/.env.example backend/pyproject.toml backend/uv.lock backend/tests
git commit -m "feat(storage): media and staging ports over Garage, verified against the pinned image"
```

---

## Task 3: The media pipeline — validate, re-encode, address by content

§10.4's pipeline, and the security control hiding inside it: re-encoding to raster is what destroys an embedded payload, which is why SVG is refused rather than sanitised.

**Files:**
- Create: `backend/src/triviador/media/__init__.py`, `backend/src/triviador/media/pipeline.py`
- Create: `backend/src/triviador/services/admin.py` (the admin ports; grows in every later task)
- Create: `backend/src/triviador/db/repositories/media.py`
- Create: `backend/src/triviador/api/schemas/admin/__init__.py`, `.../media.py`
- Create: `backend/src/triviador/api/http/admin/media.py`
- Modify: `backend/src/triviador/api/deps.py` (`media_store`, `media_assets`, `normalizer` fields)
- Modify: `backend/src/triviador/api/app.py` (build them), `backend/src/triviador/api/http/admin/__init__.py` (include the router)
- Modify: `backend/pyproject.toml` (add `pillow`, `types-pillow` if needed)
- Test: `backend/tests/media/__init__.py`, `backend/tests/media/test_pipeline.py` (pure)
- Test: `backend/tests/api/test_admin_media.py`, `backend/tests/api/fakes.py` (new fakes)
- Test: `backend/tests/db/test_media_repository.py`

**Interfaces:**
- Consumes: `MediaStore` (Task 2), `AdminPrincipal` (Task 1), `Settings.media_max_bytes/.media_max_pixels/.media_target_px` (Task 2).
- Produces:
  - `triviador.media.pipeline.normalize(raw, *, max_bytes, max_pixels, target_px) -> NormalizedImage`
  - `triviador.media.pipeline.NormalizedImage(data, sha256, width, height, mime_type, storage_key)`
  - `triviador.media.pipeline.MediaRejected(reason: str)`
  - `triviador.media.pipeline.ImageNormalizer(*, max_bytes, max_pixels, target_px).normalize(raw) -> NormalizedImage`
  - `triviador.services.admin.MediaAssetRecord`, `MediaAssetPort`
  - `triviador.db.repositories.media.MediaAssetRepository`
  - `POST /api/admin/media` → `MediaAssetSummary(id, url, width, height, byte_size)`; 201 on first upload, 200 on a repeat

- [ ] **Step 1: Write the failing pipeline test**

Create `backend/tests/media/__init__.py` (empty) and `backend/tests/media/test_pipeline.py`:

```python
"""Pure: no database, no object store, no event loop.

Every assertion here is a §10.4 sentence. The one that is not obviously a
security control — "re-encode to WebP" — is the strongest one: it is what
makes an uploaded file stop being the attacker's file.
"""

import io

import pytest
from PIL import Image

from triviador.media.pipeline import MediaRejected, normalize

LIMITS = {"max_bytes": 5_242_880, "max_pixels": 4000, "target_px": 1280}

SVG = b'<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>'


def png(width: int, height: int, colour: tuple[int, int, int] = (10, 20, 30)) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (width, height), colour).save(buffer, format="PNG")
    return buffer.getvalue()


def jpeg_with_exif(width: int = 64, height: int = 64) -> bytes:
    buffer = io.BytesIO()
    image = Image.new("RGB", (width, height), (200, 100, 50))
    exif = image.getexif()
    exif[0x010F] = "SecretCameraMaker"   # Make
    exif[0x9286] = "GPS-tagged holiday"  # UserComment
    image.save(buffer, format="JPEG", exif=exif)
    return buffer.getvalue()


def test_a_png_becomes_a_webp() -> None:
    result = normalize(png(64, 48), **LIMITS)
    assert result.mime_type == "image/webp"
    assert result.data[:4] == b"RIFF" and result.data[8:12] == b"WEBP"
    assert (result.width, result.height) == (64, 48)


def test_the_key_is_the_content_hash() -> None:
    """Content addressing is what makes re-upload, re-import and
    `media-gc` idempotent, so the key is derived from the *output* bytes,
    never from the filename or the input."""
    result = normalize(png(64, 48), **LIMITS)
    assert result.storage_key == f"{result.sha256[:2]}/{result.sha256}.webp"
    assert normalize(png(64, 48), **LIMITS).sha256 == result.sha256


def test_a_larger_image_is_downscaled_to_the_target() -> None:
    result = normalize(png(3200, 1600), **LIMITS)
    assert max(result.width, result.height) == 1280
    assert (result.width, result.height) == (1280, 640)


def test_a_smaller_image_is_not_upscaled() -> None:
    result = normalize(png(200, 100), **LIMITS)
    assert (result.width, result.height) == (200, 100)


def test_exif_does_not_survive() -> None:
    """§10.4 strips metadata. A holiday photo carries GPS coordinates, and
    an admin uploading one to a quiz question has not consented to
    publishing their home address on an anonymously readable bucket."""
    result = normalize(jpeg_with_exif(), **LIMITS)
    with Image.open(io.BytesIO(result.data)) as reencoded:
        assert not dict(reencoded.getexif())
    assert b"SecretCameraMaker" not in result.data


def test_svg_is_refused() -> None:
    """Not sanitised — refused. SVG executes script, and the pipeline's
    whole defence is that the bytes served are bytes we produced."""
    with pytest.raises(MediaRejected, match="image"):
        normalize(SVG, **LIMITS)


def test_a_payload_hidden_in_a_raster_does_not_survive_the_re_encode() -> None:
    raw = png(64, 64) + b"<script>alert('appended')</script>"
    result = normalize(raw, **LIMITS)
    assert b"<script>" not in result.data


def test_an_oversized_upload_is_refused_before_decoding() -> None:
    with pytest.raises(MediaRejected, match="5242880"):
        normalize(b"x" * 5_242_881, **LIMITS)


def test_an_image_beyond_the_pixel_bound_is_refused() -> None:
    """Checked from the header, before `load()`: decoding first is how a
    decompression bomb gets to allocate its gigabyte."""
    with pytest.raises(MediaRejected, match="4000"):
        normalize(png(4001, 10), **LIMITS)


def test_a_truncated_file_is_a_rejection_not_a_crash() -> None:
    with pytest.raises(MediaRejected):
        normalize(png(64, 64)[:60], **LIMITS)
```

- [ ] **Step 2: Run it and watch it fail**

Run: `cd backend && uv add pillow && uv run pytest tests/media -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'triviador.media'`.

- [ ] **Step 3: Write the pipeline**

Create `backend/src/triviador/media/__init__.py`:

```python
"""Everything that turns an uploaded file into a servable asset.

Pure functions plus one small class that owns a semaphore. No session, no
client, no FastAPI — `tests/test_layering.py` enforces it, and it is what
lets `tests/media/` run with both containers stopped.
"""
```

Create `backend/src/triviador/media/pipeline.py`:

```python
"""§10.4: `upload → validate → re-encode → sha256 → key → row`.

**Order matters, and it is the order written here.** Format and dimensions
are read from the header *before* `load()` decodes anything: a 40000×40000
PNG is 200 KB on the wire and 6 GB in memory, and a bound checked after
decoding is a bound checked after the damage.

**The re-encode is the security control.** Not the mime check — a mime
type is a claim by the uploader — but the fact that the bytes we store are
bytes Pillow wrote from a decoded pixel buffer. Anything smuggled in the
original (appended script, EXIF payload, polyglot header) is not copied
because nothing is copied.
"""

import asyncio
import hashlib
import io
from dataclasses import dataclass

from PIL import Image, UnidentifiedImageError

# Raster formats a quiz question plausibly uses. SVG is absent by design
# (§10.4) and cannot be added here — Pillow does not decode it, so the
# refusal is structural rather than a list entry someone can extend.
ALLOWED_FORMATS = frozenset({"PNG", "JPEG", "WEBP", "GIF", "BMP"})

WEBP_QUALITY = 82


class MediaRejected(Exception):
    """The upload is not usable, and the reason is safe to show an admin."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


@dataclass(frozen=True)
class NormalizedImage:
    data: bytes
    sha256: str
    width: int
    height: int
    mime_type: str = "image/webp"

    @property
    def byte_size(self) -> int:
        return len(self.data)

    @property
    def storage_key(self) -> str:
        """§10.4's `/data/media/<ab>/<sha>.webp`, as an object key.

        The two-character fan-out is pointless in an object store, which
        has no directory to slow down — it is kept because the spec names
        this layout, a filesystem restore of the bucket benefits from it,
        and changing the key shape later rewrites every stored row.
        """
        return f"{self.sha256[:2]}/{self.sha256}.webp"


def normalize(raw: bytes, *, max_bytes: int, max_pixels: int, target_px: int) -> NormalizedImage:
    if len(raw) > max_bytes:
        raise MediaRejected(f"image is {len(raw)} bytes; the limit is {max_bytes}")

    try:
        with Image.open(io.BytesIO(raw)) as image:
            image_format = (image.format or "").upper()
            if image_format not in ALLOWED_FORMATS:
                raise MediaRejected(
                    f"{image_format or 'this file'} is not an accepted image format; "
                    f"use one of {', '.join(sorted(ALLOWED_FORMATS))}"
                )
            if max(image.size) > max_pixels:
                raise MediaRejected(
                    f"image is {image.width}x{image.height}; the limit is {max_pixels} px"
                )
            # `convert` forces the decode, so a truncated file fails here
            # rather than halfway through `save`. RGB drops alpha and any
            # palette — WebP would keep both, and neither survives a
            # question thumbnail usefully.
            frame = image.convert("RGB")
    except UnidentifiedImageError as exc:
        raise MediaRejected("that file is not an image this server can decode") from exc
    except Image.DecompressionBombError as exc:
        raise MediaRejected("image is implausibly large when decoded") from exc
    except OSError as exc:
        raise MediaRejected("image is corrupt or truncated") from exc

    # `thumbnail` only ever shrinks, preserves the aspect ratio, and is a
    # no-op below the target — which is exactly §10.4's "max 1280 px".
    frame.thumbnail((target_px, target_px))
    buffer = io.BytesIO()
    # No `exif=`, no `icc_profile=`: metadata is dropped by omission,
    # which is stronger than stripping it afterwards.
    frame.save(buffer, format="WEBP", quality=WEBP_QUALITY, method=4)
    data = buffer.getvalue()
    return NormalizedImage(
        data=data,
        sha256=hashlib.sha256(data).hexdigest(),
        width=frame.width,
        height=frame.height,
    )


class ImageNormalizer:
    """§9.2: one encode at a time, off the event loop.

    A 200-image bulk import shares a process with live games (ADR-002).
    Unbounded decoding there stalls command processing for every match in
    flight — and `to_thread` alone would only move the stall into 200
    threads competing for the same cores. The semaphore is what bounds it.

    Built in the composition root and passed around, never a module-level
    global: an `asyncio.Semaphore` at import time is shared by every test
    in a session, which is how a suite becomes order-dependent.
    """

    def __init__(self, *, max_bytes: int, max_pixels: int, target_px: int) -> None:
        self._semaphore = asyncio.Semaphore(1)
        self._limits = {
            "max_bytes": max_bytes,
            "max_pixels": max_pixels,
            "target_px": target_px,
        }

    async def normalize(self, raw: bytes) -> NormalizedImage:
        async with self._semaphore:
            return await asyncio.to_thread(normalize, raw, **self._limits)
```

- [ ] **Step 4: Run the pipeline test**

Run: `cd backend && uv run pytest tests/media -q`
Expected: PASS (10 tests).

- [ ] **Step 5: Write the failing concurrency test**

Append to `backend/tests/media/test_pipeline.py`:

```python
async def test_only_one_encode_runs_at_a_time(monkeypatch: pytest.MonkeyPatch) -> None:
    """The semaphore, asserted rather than assumed: without it, ten
    concurrent uploads decode ten images on ten threads while a game is
    waiting for its next command."""
    import triviador.media.pipeline as pipeline_module

    live = 0
    peak = 0
    real = pipeline_module.normalize

    def instrumented(raw: bytes, **kwargs: int) -> pipeline_module.NormalizedImage:
        nonlocal live, peak
        live += 1
        peak = max(peak, live)
        try:
            return real(raw, **kwargs)
        finally:
            live -= 1

    monkeypatch.setattr(pipeline_module, "normalize", instrumented)
    normalizer = pipeline_module.ImageNormalizer(**LIMITS)
    await asyncio.gather(*(normalizer.normalize(png(64, 64, (i, i, i))) for i in range(10)))
    assert peak == 1
```

...with `import asyncio` at the top of the module and `pytestmark = pytest.mark.asyncio` added.

- [ ] **Step 6: Run it**

Run: `cd backend && uv run pytest tests/media -q`
Expected: PASS (11 tests). `ImageNormalizer.normalize` must call the module-level name (`await asyncio.to_thread(normalize, ...)` resolves through the module globals), or `monkeypatch` cannot see it — if the test fails with `peak == 10`, that is why.

- [ ] **Step 7: Declare the media port and write the repository**

Create `backend/src/triviador/services/admin.py`:

```python
"""What the admin surface asks of the database, as Protocols.

Same rule as `ports.py` and `identity.py`: `api/` depends on these, `db/`
implements them, neither imports the other, and `tests/api/` runs the
whole admin surface against in-memory fakes with no PostgreSQL.

One port per resource rather than one `AdminPort` with thirty methods:
`tests/api/fakes.py` has to implement whatever a route touches, and a
single wide port would make every fake grow a method for every route in
the plan.
"""

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class MediaAssetRecord:
    asset_id: str
    mime_type: str
    width: int | None
    height: int | None
    byte_size: int
    storage_key: str


class MediaAssetPort(Protocol):
    async def ensure(
        self,
        *,
        asset_id: str,
        mime_type: str,
        width: int,
        height: int,
        byte_size: int,
        storage_key: str,
        created_by: str,
    ) -> tuple[MediaAssetRecord, bool]:
        """The record, and whether this call created it.

        Two admins uploading the same image produce the same sha256 and so
        the same row; the boolean is what lets the route answer 201 the
        first time and 200 afterwards rather than raising on a primary-key
        collision that means "this already worked".
        """
        ...

    async def get(self, asset_id: str) -> MediaAssetRecord | None: ...
```

Create `backend/src/triviador/db/repositories/media.py`:

```python
"""`media_assets`, whose primary key is the content hash (Plan 3's model).

`ensure` is `INSERT ... ON CONFLICT DO NOTHING` followed by a read, not
`SELECT`-then-`INSERT`: the two-statement form races two concurrent
uploads of the same image into one `UniqueViolation`, and the losing
admin's upload — which succeeded in every way that matters, the blob is
written and identical — would fail.
"""

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from triviador.db.models.content import MediaAsset
from triviador.services.admin import MediaAssetRecord


def _to_record(row: MediaAsset) -> MediaAssetRecord:
    return MediaAssetRecord(
        asset_id=row.id,
        mime_type=row.mime_type,
        width=row.width,
        height=row.height,
        byte_size=row.byte_size,
        storage_key=row.storage_key,
    )


class MediaAssetRepository:
    """Implements `services.admin.MediaAssetPort`."""

    def __init__(self, sessionmaker: async_sessionmaker[AsyncSession]) -> None:
        self._sessionmaker = sessionmaker

    async def ensure(
        self,
        *,
        asset_id: str,
        mime_type: str,
        width: int,
        height: int,
        byte_size: int,
        storage_key: str,
        created_by: str,
    ) -> tuple[MediaAssetRecord, bool]:
        async with self._sessionmaker() as session, session.begin():
            inserted = await session.execute(
                insert(MediaAsset)
                .values(
                    id=asset_id,
                    mime_type=mime_type,
                    width=width,
                    height=height,
                    byte_size=byte_size,
                    storage_key=storage_key,
                    created_by=created_by,
                )
                .on_conflict_do_nothing(index_elements=[MediaAsset.id])
                .returning(MediaAsset)
            )
            row = inserted.scalar_one_or_none()
            if row is not None:
                return _to_record(row), True
            existing = await session.execute(select(MediaAsset).where(MediaAsset.id == asset_id))
            return _to_record(existing.scalar_one()), False

    async def get(self, asset_id: str) -> MediaAssetRecord | None:
        async with self._sessionmaker() as session:
            row = await session.get(MediaAsset, asset_id)
        return None if row is None else _to_record(row)
```

Create `backend/tests/db/test_media_repository.py` (integration, following `tests/db/conftest.py`'s module-level marks):

```python
"""`pytestmark` per module, `loop_scope="session"` per async test — the
discipline `tests/db/conftest.py`'s docstring explains."""

import pytest

from triviador.db.repositories.media import MediaAssetRepository

pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="session")]


async def test_ensure_is_idempotent_and_reports_which_call_created_the_row(
    sessions, clean_db
) -> None:
    await _seed_user(sessions, "admin-1")
    repository = MediaAssetRepository(sessions)
    first, created = await repository.ensure(
        asset_id="a" * 64,
        mime_type="image/webp",
        width=100,
        height=50,
        byte_size=1234,
        storage_key="aa/aaa.webp",
        created_by="admin-1",
    )
    second, created_again = await repository.ensure(
        asset_id="a" * 64,
        mime_type="image/webp",
        width=100,
        height=50,
        byte_size=1234,
        storage_key="aa/aaa.webp",
        created_by="admin-1",
    )
    assert created is True and created_again is False
    assert first == second
```

...importing `_seed_user` from `tests.db.conftest`.

- [ ] **Step 8: Write the failing route test**

Add the fakes to `backend/tests/api/fakes.py`:

```python
class FakeMediaStore:
    """In-memory `MediaStore`. Keeps `put` calls so a test can assert the
    `Cache-Control` the route asked for without a live Garage."""

    def __init__(self, clock: FakeClock | None = None) -> None:
        self.objects: dict[str, bytes] = {}
        self.metadata: dict[str, tuple[str, str | None]] = {}
        # Write times, so a test can age an object past the gc grace
        # period without sleeping.
        self.written: dict[str, datetime] = {}
        self._clock = clock or FakeClock()

    async def put(
        self, key: str, data: bytes, *, content_type: str, cache_control: str | None = None
    ) -> None:
        self.objects[key] = data
        self.metadata[key] = (content_type, cache_control)
        self.written[key] = self._clock.now()

    async def open(self, key: str) -> bytes | None:
        return self.objects.get(key)

    async def head(self, key: str) -> ObjectHead | None:
        if key not in self.objects:
            return None
        content_type, cache_control = self.metadata[key]
        return ObjectHead(
            len(self.objects[key]), content_type, cache_control, self.written[key]
        )

    async def delete(self, key: str) -> None:
        self.objects.pop(key, None)

    async def list_objects(self, *, prefix: str = "") -> tuple[StoredObject, ...]:
        return tuple(
            StoredObject(key=key, byte_size=len(self.objects[key]), last_modified=self.written[key])
            for key in sorted(self.objects)
            if key.startswith(prefix)
        )


class FakeMediaAssets:
    """In-memory `MediaAssetPort`."""

    def __init__(self) -> None:
        self.records: dict[str, MediaAssetRecord] = {}

    async def ensure(self, **kwargs: object) -> tuple[MediaAssetRecord, bool]:
        asset_id = str(kwargs["asset_id"])
        if asset_id in self.records:
            return self.records[asset_id], False
        record = MediaAssetRecord(
            asset_id=asset_id,
            mime_type=str(kwargs["mime_type"]),
            width=int(kwargs["width"]),  # type: ignore[arg-type]
            height=int(kwargs["height"]),  # type: ignore[arg-type]
            byte_size=int(kwargs["byte_size"]),  # type: ignore[arg-type]
            storage_key=str(kwargs["storage_key"]),
        )
        self.records[asset_id] = record
        return record, True

    async def get(self, asset_id: str) -> MediaAssetRecord | None:
        return self.records.get(asset_id)
```

Create `backend/tests/api/test_admin_media.py`:

```python
import httpx
import pytest

from tests.api.conftest import ORIGIN
from tests.media.test_pipeline import SVG, png
from triviador.api.deps import AppDependencies

pytestmark = pytest.mark.asyncio


async def _upload(client: httpx.AsyncClient, body: bytes, content_type: str) -> httpx.Response:
    return await client.post(
        "/api/admin/media", content=body, headers={"Content-Type": content_type, "Origin": ORIGIN}
    )


async def test_a_player_cannot_upload(signed_in: httpx.AsyncClient) -> None:
    assert (await _upload(signed_in, png(8, 8), "image/png")).status_code == 403


async def test_an_upload_is_stored_re_encoded_and_addressed_by_content(
    admin_client: httpx.AsyncClient, deps: AppDependencies
) -> None:
    response = await _upload(admin_client, png(64, 32), "image/png")
    assert response.status_code == 201
    body = response.json()
    assert body["width"] == 64 and body["height"] == 32
    assert body["url"] == f"/media/{body['id'][:2]}/{body['id']}.webp"
    stored = deps.media_store.objects[f"{body['id'][:2]}/{body['id']}.webp"]
    assert stored[:4] == b"RIFF"


async def test_the_object_carries_the_immutable_cache_header(
    admin_client: httpx.AsyncClient, deps: AppDependencies
) -> None:
    body = (await _upload(admin_client, png(8, 8), "image/png")).json()
    key = f"{body['id'][:2]}/{body['id']}.webp"
    assert deps.media_store.metadata[key] == (
        "image/webp",
        "public, max-age=31536000, immutable",
    )


async def test_re_uploading_the_same_image_answers_200_with_the_same_id(
    admin_client: httpx.AsyncClient
) -> None:
    first = await _upload(admin_client, png(16, 16), "image/png")
    second = await _upload(admin_client, png(16, 16), "image/png")
    assert (first.status_code, second.status_code) == (201, 200)
    assert first.json()["id"] == second.json()["id"]


async def test_an_svg_is_refused_with_a_reason(admin_client: httpx.AsyncClient) -> None:
    response = await _upload(admin_client, SVG, "image/svg+xml")
    assert response.status_code == 415
    assert response.json()["code"] == "media_rejected"


async def test_a_blob_deleted_between_put_and_row_is_restored(
    admin_client: httpx.AsyncClient, deps: AppDependencies
) -> None:
    """Decision 9's repair, driven directly: with the row committed and
    the object gone, the route must put it back rather than answer 201 for
    an asset that is not there."""
    body = png(24, 24)
    first = (await _upload(admin_client, body, "image/png")).json()
    key = f"{first['id'][:2]}/{first['id']}.webp"
    del deps.media_store.objects[key]
    second = await _upload(admin_client, body, "image/png")
    assert second.status_code == 200
    assert key in deps.media_store.objects


async def test_a_body_over_the_media_cap_is_refused_by_the_route(
    admin_client: httpx.AsyncClient, deps: AppDependencies
) -> None:
    """The global body limit does not apply here (Task 1); the route's own
    cap does, and it stops reading rather than buffering the whole body."""
    oversized = b"x" * (deps.settings.media_max_bytes + 1)
    response = await _upload(admin_client, oversized, "image/png")
    assert response.status_code == 413
    assert response.json()["code"] == "payload_too_large"


def test_every_exempt_upload_path_is_a_real_route(deps: AppDependencies) -> None:
    """`UPLOAD_PATHS` is a hole in the body limit. A stale entry is a hole
    pointing at nothing, and a renamed route is a route that silently
    starts buffering at 1 MiB again.

    `api_routes` rather than `app.routes`: the latter holds
    `_IncludedRouter` wrappers, so an `isinstance(r, APIRoute)` filter over
    it returns nothing and this assertion would pass on any input (Task 1
    established this; its `test_the_walk_reaches_real_routes` is the
    tripwire).
    """
    from tests.api.conftest import api_routes
    from triviador.api.app import create_app
    from triviador.api.http.admin import UPLOAD_PATHS

    paths = {mounted.path for mounted in api_routes(create_app(deps))}
    assert set(UPLOAD_PATHS) - paths == {"/api/admin/questions/import/dry-run"}
```

(The last assertion carries its own reminder: Task 7 lands the import route, and its step there changes this to `== set()`.)

- [ ] **Step 9: Run it and watch it fail**

Run: `cd backend && uv run pytest tests/api/test_admin_media.py -q`
Expected: FAIL — 404 on `/api/admin/media`.

- [ ] **Step 10: Write the schema and the route**

Create `backend/src/triviador/api/schemas/admin/__init__.py`:

```python
"""Admin DTOs. `contracts.py` imports from here; nothing else does."""
```

Create `backend/src/triviador/api/schemas/admin/media.py`:

```python
from pydantic import BaseModel, ConfigDict


class MediaAssetSummary(BaseModel):
    """What the editor needs to show a thumbnail and store a reference.

    `url` is built by the server from `media_public_base`, exactly as the
    projection does for in-game question media (§8.7) — the client never
    concatenates a base with a key, so the two can never disagree.
    """

    model_config = ConfigDict(extra="forbid")

    id: str
    url: str
    width: int | None
    height: int | None
    byte_size: int
```

Create `backend/src/triviador/api/http/admin/media.py`:

```python
"""`POST /api/admin/media`: one image, one blob, one row.

**A raw body, not multipart.** §10.1's surface says only "media upload".
Multipart would buy a filename we do not store (the key is the content
hash) and a form field we do not have, in exchange for parsing a format
whose bounds are hard to enforce while streaming. The client sends the
file as the body with its own `Content-Type`; the type is a hint the
pipeline ignores, since the format is read from the bytes.

**Order: blob first, row second.** A failed row insert leaves an
unreferenced blob that `media-gc` collects. The reverse leaves a row
pointing at nothing, which is a broken question nobody can repair.
"""

from fastapi import APIRouter, Request

from triviador.api.deps import AdminPrincipal, Deps
from triviador.api.errors import ApiError, ApiErrorCode
from triviador.api.schemas.admin.media import MediaAssetSummary
from triviador.media.pipeline import MediaRejected, NormalizedImage

router = APIRouter(tags=["admin"])

CACHE_CONTROL = "public, max-age=31536000, immutable"


async def read_capped(request: Request, max_bytes: int) -> bytes:
    """Read the stream, refusing as soon as the cap is passed.

    Shared with the import route (Task 7). Reading to the end and then
    checking the length would hold 32 MiB of somebody else's problem in
    memory before answering 413 — which is the same reasoning
    `BodyLimitMiddleware` gives, applied at the route that opted out of it.
    """
    chunks: list[bytes] = []
    total = 0
    async for chunk in request.stream():
        total += len(chunk)
        if total > max_bytes:
            raise ApiError(
                ApiErrorCode.PAYLOAD_TOO_LARGE, 413, f"upload exceeds {max_bytes} bytes"
            )
        chunks.append(chunk)
    return b"".join(chunks)


async def repair_blob(deps: Deps, image: NormalizedImage) -> None:
    """Make sure the object is still there now that the row is committed.

    The window this closes: `media-gc`'s orphan pass deletes objects with
    no database row, and between this route's `put` and its `ensure` there
    *is* no row. A sweep running in that instant takes the blob and leaves
    a row pointing at nothing — a broken image nobody can repair from the
    editor, because re-uploading the same file produces the same content
    hash and finds the row already present.

    `media-gc` also skips objects younger than `media_gc_grace_minutes`,
    so this repair should never fire. It costs one `HEAD` on a LAN and is
    the only fix that needs no lock and no transaction spanning a network
    write — see Decision 9.
    """
    if await deps.media_store.head(image.storage_key) is None:
        await deps.media_store.put(
            image.storage_key,
            image.data,
            content_type=image.mime_type,
            cache_control=CACHE_CONTROL,
        )


def summary(image_id: str, *, media_base: str, width: int | None, height: int | None,
            byte_size: int) -> MediaAssetSummary:
    return MediaAssetSummary(
        id=image_id,
        url=f"{media_base}/{image_id[:2]}/{image_id}.webp",
        width=width,
        height=height,
        byte_size=byte_size,
    )


@router.post("/media", status_code=201)
async def upload_media(
    request: Request, response: "Response", deps: Deps, principal: AdminPrincipal
) -> MediaAssetSummary:
    raw = await read_capped(request, deps.settings.media_max_bytes)
    try:
        image: NormalizedImage = await deps.normalizer.normalize(raw)
    except MediaRejected as exc:
        # 415, not 422: the request was well-formed, its *media type* is
        # the thing this server will not accept.
        raise ApiError(ApiErrorCode.MEDIA_REJECTED, 415, exc.reason) from exc

    await deps.media_store.put(
        image.storage_key,
        image.data,
        content_type=image.mime_type,
        cache_control=CACHE_CONTROL,
    )
    record, created = await deps.media_assets.ensure(
        asset_id=image.sha256,
        mime_type=image.mime_type,
        width=image.width,
        height=image.height,
        byte_size=image.byte_size,
        storage_key=image.storage_key,
        created_by=str(principal.user_id),
    )
    await repair_blob(deps, image)
    if not created:
        response.status_code = 200
    return summary(
        record.asset_id,
        media_base=deps.settings.media_public_base,
        width=record.width,
        height=record.height,
        byte_size=record.byte_size,
    )
```

...with `from fastapi import APIRouter, Request, Response` and the quotes removed from the annotation.

Add `MEDIA_REJECTED = "media_rejected"` to `ApiErrorCode` in `backend/src/triviador/api/errors.py`, and `415: ApiErrorCode.MEDIA_REJECTED` to `_STATUS_CODES`.

Include the router: in `backend/src/triviador/api/http/admin/__init__.py`, `from triviador.api.http.admin import media` and `router = build_admin_router(media.router)`.

- [ ] **Step 11: Wire the dependencies**

In `backend/src/triviador/api/deps.py`, add three fields to `AppDependencies`:

```python
    media_store: MediaStore
    media_assets: MediaAssetPort
    normalizer: ImageNormalizer
```

...and to `placeholder()`: `media_store=unusable, media_assets=unusable,` plus a real `normalizer=ImageNormalizer(max_bytes=1, max_pixels=1, target_px=1)` — it constructs without touching anything, and `export_contracts` never calls it.

In `backend/src/triviador/api/app.py`'s `build_dependencies`:

```python
    media_store = S3MediaStore(
        endpoint_url=settings.s3_endpoint_url,
        region=settings.s3_region,
        access_key_id=settings.s3_access_key_id,
        secret_access_key=settings.s3_secret_access_key.get_secret_value(),
        bucket=settings.media_bucket,
    )
```

...passed as `media_store=media_store`, with `media_assets=MediaAssetRepository(sessions)` and

```python
        normalizer=ImageNormalizer(
            max_bytes=settings.media_max_bytes,
            max_pixels=settings.media_max_pixels,
            target_px=settings.media_target_px,
        ),
```

In `backend/tests/api/conftest.py`'s `deps` fixture, add `media_store=FakeMediaStore()`, `media_assets=FakeMediaAssets()`, and a real `ImageNormalizer` built from the fixture's `settings`.

- [ ] **Step 12: Write the wiring test `services/storage.py` already promises**

Task 2's `services/storage.py` docstring says "`tests/api/test_admin_wiring.py` asserts the two
adapters carry different bucket names" — and until this step, that file does not exist. The claim
matters: the two ports are structurally interchangeable (`MediaStore` is a superset of
`ImportStagingStore`), so nothing but the composition root stops the staging adapter — holding raw
uploads with unpublished answer keys — from being handed to a route that writes to the
anonymously-readable media bucket. The type system cannot catch that swap; this test can.

Create `backend/tests/api/test_admin_wiring.py`:

```python
"""The composition root is the only thing that tells the two object stores
apart (§9.1). `services/storage.py`'s docstring says so; this file is what
makes the claim true.
"""

import pytest
from pydantic import SecretStr

from triviador.api.app import build_dependencies
from triviador.config import Settings


@pytest.fixture
def wired_settings() -> Settings:
    return Settings(
        database_url="postgresql+asyncpg://unused/unused",
        allowed_origins=("http://box.lan",),
        s3_access_key_id="GK111111111111111111111111",
        s3_secret_access_key=SecretStr("2" * 64),
    )


def test_the_two_stores_are_bound_to_different_buckets(wired_settings: Settings) -> None:
    """Swapping them would publish raw import uploads — answer keys
    included — to the anonymously readable bucket, and no type error would
    be raised, because `MediaStore` structurally satisfies
    `ImportStagingStore`.
    """
    built = build_dependencies(wired_settings)
    assert built.deps.media_store.bucket == wired_settings.media_bucket
    assert built.deps.staging_store.bucket == wired_settings.staging_bucket
    assert built.deps.media_store.bucket != built.deps.staging_store.bucket
```

`build_dependencies` constructs an `AsyncEngine` but opens no connection, so this test needs no
database. `staging_store` arrives in Task 7 — until then, assert only the media half and add the
staging assertions in that task's wiring step.

- [ ] **Step 13: Run everything and commit**

Run: `cd backend && uv run pytest -q && uv run mypy && uv run ruff check .`
Expected: PASS.

```bash
git add backend/src/triviador backend/tests backend/pyproject.toml backend/uv.lock
git commit -m "feat(admin): media upload — validate, re-encode to WebP, address by content"
```

---

## Task 4: The question list — pagination, filters, and a search that does not pick a language

**Files:**
- Create: `backend/src/triviador/db/migrations/versions/0004_question_search.py`
- Create: `backend/src/triviador/db/repositories/question_admin.py`
- Create: `backend/src/triviador/api/schemas/admin/questions.py`, `backend/src/triviador/api/http/admin/questions.py`
- Modify: `backend/src/triviador/services/admin.py` (question records + port)
- Modify: `backend/src/triviador/api/http/admin/__init__.py`, `backend/src/triviador/api/deps.py`, `backend/src/triviador/api/app.py`, `backend/tests/api/conftest.py`, `backend/tests/api/fakes.py`
- Test: `backend/tests/db/test_question_admin.py`, `backend/tests/api/test_admin_questions.py`, `backend/tests/db/test_migrations.py` (extend)

**Interfaces:**
- Produces:
  - `services.admin.QuestionFilters(kind, category_id, difficulty, is_active, has_media, search)`
  - `services.admin.QuestionSummaryRecord(question_id, kind, prompt, category_slug, difficulty, is_active, has_media, version, updated_at)`
  - `services.admin.QuestionDetailRecord(..., choices: tuple[ChoiceRecord, ...] | None, numeric_answer: Decimal | None, unit: str | None, media_asset_id: str | None)`
  - `services.admin.QuestionPage(items: tuple[QuestionSummaryRecord, ...], total: int)`
  - `services.admin.QuestionAdminPort.list(filters, *, limit, offset)`, `.get(question_id)`
  - `GET /api/admin/questions?kind=&category_id=&difficulty=&is_active=&has_media=&q=&limit=&offset=` → `QuestionPage`
  - `GET /api/admin/questions/{id}` → `QuestionDetail`

- [ ] **Step 1: Write the failing migration test**

Extend `backend/tests/db/test_migrations.py` with:

```python
async def test_the_prompt_search_index_exists_and_is_a_trigram_index(engine, migrated_schema):
    """A plain b-tree on `prompt` would be created without error and used
    for nothing: `ILIKE '%needle%'` cannot use it. Asserting the *kind* of
    index is asserting that the search is actually indexed."""
    async with engine.connect() as connection:
        row = (
            await connection.execute(
                text(
                    "SELECT indexdef FROM pg_indexes "
                    "WHERE tablename = 'questions' AND indexname = 'ix_questions_prompt_trgm'"
                )
            )
        ).scalar_one_or_none()
    assert row is not None
    assert "gin" in row.lower() and "gin_trgm_ops" in row.lower()
```

- [ ] **Step 2: Run it and watch it fail**

Run: `cd backend && uv run pytest tests/db/test_migrations.py -q`
Expected: FAIL — `assert None is not None`.

- [ ] **Step 3: Write the migration**

Create `backend/src/triviador/db/migrations/versions/0004_question_search.py`:

```python
"""Trigram index for §10.2's prompt search.

`pg_trgm` + `ILIKE '%needle%'` rather than a `tsvector`: PostgreSQL ships
no Czech text-search configuration, this deployment's map is Czechia and
its seed bank is English, so any stemming configuration chosen here is the
wrong one for half the bank. Trigrams are language-independent, and
substring is what an admin who half-remembers a question actually types.

The index is on `lower(prompt)`, and the query must be
`lower(prompt) LIKE lower(:needle)` for the planner to use it — `ILIKE`
against the bare column would not match this expression index.

`CREATE EXTENSION` needs privileges an unprivileged application role does
not have. In this deployment the migration runs as the owner of its own
database (§10.5's `migrate` service), which does. If a future deployment
splits those roles, this line moves to a provisioning step and the
migration keeps only the index.

Revision ID: 0004_question_search
Revises: 0003_repair_default_preset_rules
"""

from alembic import op

revision = "0004_question_search"
down_revision = "0003_repair_default_preset_rules"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")
    op.execute(
        "CREATE INDEX ix_questions_prompt_trgm ON questions "
        "USING gin (lower(prompt) gin_trgm_ops)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_questions_prompt_trgm")
    # The extension is deliberately not dropped: another schema may be
    # using it, and `DROP EXTENSION` would take their indexes with it.
```

Run: `cd backend && uv run pytest tests/db/test_migrations.py -q` → PASS.

- [ ] **Step 4: Write the failing repository test**

Create `backend/tests/db/test_question_admin.py`:

```python
import pytest

from triviador.db.repositories.question_admin import QuestionAdminRepository
from triviador.services.admin import QuestionFilters

pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="session")]


async def _bank(sessions) -> None:
    await _seed_category(sessions)
    await _seed_category(sessions, "cat-2", slug="film", name="Film")
    await _seed_mc_question(sessions, "q-mc", prompt="Who painted the Velvet Revolution mural?")
    await _seed_numeric_question(sessions, "q-num", prompt="In which year did it begin?")
    await _seed_mc_question(sessions, "q-off", prompt="Retired question", is_active=False)


async def test_the_list_pages_and_reports_the_unpaged_total(sessions, clean_db) -> None:
    await _bank(sessions)
    page = await QuestionAdminRepository(sessions).list(QuestionFilters(), limit=2, offset=0)
    assert len(page.items) == 2
    assert page.total == 3


async def test_search_is_a_case_insensitive_substring(sessions, clean_db) -> None:
    await _bank(sessions)
    page = await QuestionAdminRepository(sessions).list(
        QuestionFilters(search="velvet"), limit=50, offset=0
    )
    assert [q.question_id for q in page.items] == ["q-mc"]


async def test_a_percent_in_the_search_is_a_literal_not_a_wildcard(sessions, clean_db) -> None:
    """Without escaping, `%` matches everything and an admin searching for
    a question about percentages gets the whole bank back."""
    await _bank(sessions)
    page = await QuestionAdminRepository(sessions).list(
        QuestionFilters(search="%"), limit=50, offset=0
    )
    assert page.items == ()


@pytest.mark.parametrize(
    ("filters", "expected"),
    [
        (QuestionFilters(kind="numeric"), ["q-num"]),
        (QuestionFilters(is_active=False), ["q-off"]),
        (QuestionFilters(has_media=True), []),
        (QuestionFilters(difficulty="easy"), ["q-mc", "q-num", "q-off"]),
    ],
)
async def test_each_filter_narrows_the_list(sessions, clean_db, filters, expected) -> None:
    await _bank(sessions)
    page = await QuestionAdminRepository(sessions).list(filters, limit=50, offset=0)
    assert sorted(q.question_id for q in page.items) == sorted(expected)


async def test_get_returns_the_choices_and_the_numeric_answer(sessions, clean_db) -> None:
    await _bank(sessions)
    repository = QuestionAdminRepository(sessions)
    mc = await repository.get("q-mc")
    numeric = await repository.get("q-num")
    assert mc is not None and numeric is not None
    assert [c.text for c in mc.choices or ()] == ["A", "B"]
    assert numeric.choices is None and numeric.numeric_answer is not None
    assert await repository.get("nope") is None
```

`_seed_category`, `_seed_user`, `_seed_mc_question` and `_seed_numeric_question` already exist in `backend/tests/db/conftest.py` (Plan 3 wrote them, `tests/api/integration/conftest.py` already imports all four). Import them here rather than writing new ones — a second seeding helper is a second definition of what a question row looks like.

- [ ] **Step 5: Run it and watch it fail**

Run: `cd backend && uv run pytest tests/db/test_question_admin.py -q`
Expected: FAIL — `ModuleNotFoundError: triviador.db.repositories.question_admin`.

- [ ] **Step 6: Declare the records and the port**

Append to `backend/src/triviador/services/admin.py`:

```python
@dataclass(frozen=True)
class QuestionFilters:
    """§10.2's filter set. Every field is `None` for "do not filter",
    which is why `is_active` is `bool | None` and not `bool`: the admin
    list defaults to *everything*, and a `False` default would hide the
    active bank behind a filter nobody set."""

    kind: str | None = None
    category_id: str | None = None
    difficulty: str | None = None
    is_active: bool | None = None
    has_media: bool | None = None
    search: str | None = None


@dataclass(frozen=True)
class ChoiceRecord:
    idx: int
    text: str
    is_correct: bool
    media_asset_id: str | None


@dataclass(frozen=True)
class QuestionSummaryRecord:
    question_id: str
    kind: str
    prompt: str
    category_id: str
    category_slug: str
    difficulty: str
    is_active: bool
    has_media: bool
    version: int
    updated_at: datetime


@dataclass(frozen=True)
class QuestionDetailRecord:
    question_id: str
    kind: str
    prompt: str
    category_id: str
    category_slug: str
    difficulty: str
    is_active: bool
    version: int
    media_asset_id: str | None
    choices: tuple[ChoiceRecord, ...] | None
    numeric_answer: Decimal | None
    unit: str | None


@dataclass(frozen=True)
class QuestionPage:
    items: tuple[QuestionSummaryRecord, ...]
    total: int


class QuestionAdminPort(Protocol):
    async def list(
        self, filters: QuestionFilters, *, limit: int, offset: int
    ) -> QuestionPage: ...
    async def get(self, question_id: str) -> QuestionDetailRecord | None: ...
```

...with `from datetime import datetime` and `from decimal import Decimal` at the top.

- [ ] **Step 7: Write the repository**

Create `backend/src/triviador/db/repositories/question_admin.py`:

```python
"""The admin's read and write access to the question bank.

Deliberately not part of `repositories/questions.py`. That module is the
draw path — one method, taken under `FOR SHARE`, inside the caller's
transaction — and its docstring is an argument about locking that an
admin CRUD surface would bury. The two share only the tables.
"""

from collections import defaultdict
from datetime import datetime

from sqlalchemy import Select, func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from triviador.db.models.content import Category, Question, QuestionChoice, QuestionNumeric
from triviador.services.admin import (
    ChoiceRecord,
    QuestionDetailRecord,
    QuestionFilters,
    QuestionPage,
    QuestionSummaryRecord,
)


def _apply(statement: Select, filters: QuestionFilters) -> Select:
    if filters.kind is not None:
        statement = statement.where(Question.kind == filters.kind)
    if filters.category_id is not None:
        statement = statement.where(Question.category_id == filters.category_id)
    if filters.difficulty is not None:
        statement = statement.where(Question.difficulty == filters.difficulty)
    if filters.is_active is not None:
        statement = statement.where(Question.is_active.is_(filters.is_active))
    if filters.has_media is not None:
        statement = statement.where(
            Question.media_asset_id.is_not(None)
            if filters.has_media
            else Question.media_asset_id.is_(None)
        )
    if filters.search:
        # `lower(prompt) LIKE lower(:needle)`, matching the expression the
        # trigram index is built on (migration 0004). `autoescape` turns a
        # literal `%` or `_` in the admin's search box into a literal
        # match instead of a wildcard that returns the whole bank.
        statement = statement.where(
            func.lower(Question.prompt).contains(filters.search.lower(), autoescape=True)
        )
    return statement


class QuestionAdminRepository:
    """Implements `services.admin.QuestionAdminPort`."""

    def __init__(self, sessionmaker: async_sessionmaker[AsyncSession]) -> None:
        self._sessionmaker = sessionmaker

    async def list(self, filters: QuestionFilters, *, limit: int, offset: int) -> QuestionPage:
        async with self._sessionmaker() as session:
            rows = (
                await session.execute(
                    _apply(
                        select(Question, Category.slug).join(
                            Category, Category.id == Question.category_id
                        ),
                        filters,
                    )
                    # `id` breaks ties: two questions seeded in the same
                    # transaction share `created_at` to the microsecond,
                    # and an unstable sort makes page 2 skip and repeat
                    # rows nobody edited.
                    .order_by(Question.created_at.desc(), Question.id)
                    .limit(limit)
                    .offset(offset)
                )
            ).all()
            total = (
                await session.execute(
                    _apply(select(func.count()).select_from(Question), filters)
                )
            ).scalar_one()
        return QuestionPage(
            items=tuple(_summary(question, slug) for question, slug in rows), total=total
        )

    async def get(self, question_id: str) -> QuestionDetailRecord | None:
        async with self._sessionmaker() as session:
            row = (
                await session.execute(
                    select(Question, Category.slug)
                    .join(Category, Category.id == Question.category_id)
                    .where(Question.id == question_id)
                )
            ).one_or_none()
            if row is None:
                return None
            question, slug = row
            choices = (
                (
                    await session.execute(
                        select(QuestionChoice)
                        .where(QuestionChoice.question_id == question_id)
                        .order_by(QuestionChoice.idx)
                    )
                )
                .scalars()
                .all()
            )
            numeric = await session.get(QuestionNumeric, question_id)
        return QuestionDetailRecord(
            question_id=question.id,
            kind=question.kind,
            prompt=question.prompt,
            category_id=question.category_id,
            category_slug=slug,
            difficulty=question.difficulty,
            is_active=question.is_active,
            version=question.version,
            media_asset_id=question.media_asset_id,
            choices=(
                tuple(
                    ChoiceRecord(c.idx, c.text, c.is_correct, c.media_asset_id) for c in choices
                )
                if choices
                else None
            ),
            numeric_answer=numeric.correct_value if numeric is not None else None,
            unit=numeric.unit if numeric is not None else None,
        )


def _summary(question: Question, category_slug: str) -> QuestionSummaryRecord:
    return QuestionSummaryRecord(
        question_id=question.id,
        kind=question.kind,
        prompt=question.prompt,
        category_id=question.category_id,
        category_slug=category_slug,
        difficulty=question.difficulty,
        is_active=question.is_active,
        has_media=question.media_asset_id is not None,
        version=question.version,
        updated_at=question.updated_at,
    )
```

Run: `cd backend && uv run pytest tests/db/test_question_admin.py -q` → PASS.

- [ ] **Step 8: Write the failing route test**

Create `backend/tests/api/test_admin_questions.py` (the list half; Task 5 appends the write half):

```python
import httpx
import pytest

pytestmark = pytest.mark.asyncio


async def test_a_player_cannot_list_questions(signed_in: httpx.AsyncClient) -> None:
    assert (await signed_in.get("/api/admin/questions")).status_code == 403


async def test_the_list_answers_a_page_and_a_total(admin_client: httpx.AsyncClient) -> None:
    response = await admin_client.get("/api/admin/questions?limit=1")
    assert response.status_code == 200
    body = response.json()
    assert len(body["items"]) == 1
    assert body["total"] >= 1
    assert body["limit"] == 1 and body["offset"] == 0


async def test_the_filters_reach_the_repository(
    admin_client: httpx.AsyncClient, deps
) -> None:
    await admin_client.get(
        "/api/admin/questions?kind=numeric&is_active=true&has_media=false&q=velvet"
    )
    assert deps.questions_admin.last_filters.kind == "numeric"
    assert deps.questions_admin.last_filters.is_active is True
    assert deps.questions_admin.last_filters.has_media is False
    assert deps.questions_admin.last_filters.search == "velvet"


async def test_an_unknown_kind_is_a_validation_error_not_an_empty_page(
    admin_client: httpx.AsyncClient
) -> None:
    """A typo in a filter must not look like an empty bank."""
    response = await admin_client.get("/api/admin/questions?kind=picture")
    assert response.status_code == 422
    assert response.json()["code"] == "validation_failed"


async def test_limit_is_bounded(admin_client: httpx.AsyncClient) -> None:
    assert (await admin_client.get("/api/admin/questions?limit=5000")).status_code == 422


async def test_a_missing_question_is_404(admin_client: httpx.AsyncClient) -> None:
    response = await admin_client.get("/api/admin/questions/nope")
    assert response.status_code == 404
    assert response.json()["code"] == "not_found"
```

Add `FakeQuestionAdmin` to `backend/tests/api/fakes.py` — a dict of `QuestionDetailRecord` plus a `last_filters` attribute recorded by `list`, returning a `QuestionPage` sliced by `limit`/`offset` — and wire `questions_admin=FakeQuestionAdmin(...)` into the `deps` fixture with one seeded numeric question.

- [ ] **Step 9: Write the schemas and the route**

Create `backend/src/triviador/api/schemas/admin/questions.py`:

```python
from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict

from triviador.domain.questions.types import Difficulty, QuestionKind


class ChoiceView(BaseModel):
    model_config = ConfigDict(extra="forbid")

    idx: int
    text: str
    is_correct: bool
    media_asset_id: str | None


class QuestionSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    kind: QuestionKind
    prompt: str
    category_id: str
    category_slug: str
    difficulty: Difficulty
    is_active: bool
    has_media: bool
    version: int
    updated_at: datetime


class QuestionDetail(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    kind: QuestionKind
    prompt: str
    category_id: str
    category_slug: str
    difficulty: Difficulty
    is_active: bool
    version: int
    media_asset_id: str | None
    choices: list[ChoiceView] | None
    numeric_answer: Decimal | None
    unit: str | None


class QuestionPageView(BaseModel):
    """`total` is the unpaged count, so the client can render "page 3 of
    17" without a second request — the one thing offset pagination is
    actually good at."""

    model_config = ConfigDict(extra="forbid")

    items: list[QuestionSummary]
    total: int
    limit: int
    offset: int
```

Create `backend/src/triviador/api/http/admin/questions.py`:

```python
"""§10.2's list and editor, read half."""

from typing import Annotated

from fastapi import APIRouter, Query

from triviador.api.deps import AdminPrincipal, Deps
from triviador.api.errors import ApiError, ApiErrorCode
from triviador.api.schemas.admin.questions import (
    ChoiceView,
    QuestionDetail,
    QuestionPageView,
    QuestionSummary,
)
from triviador.domain.questions.types import Difficulty, QuestionKind
from triviador.services.admin import QuestionDetailRecord, QuestionFilters, QuestionSummaryRecord

router = APIRouter(prefix="/questions", tags=["admin"])

MAX_LIMIT = 200


def _summary(record: QuestionSummaryRecord) -> QuestionSummary:
    return QuestionSummary(
        id=record.question_id,
        kind=QuestionKind(record.kind),
        prompt=record.prompt,
        category_id=record.category_id,
        category_slug=record.category_slug,
        difficulty=Difficulty(record.difficulty),
        is_active=record.is_active,
        has_media=record.has_media,
        version=record.version,
        updated_at=record.updated_at,
    )


def detail(record: QuestionDetailRecord) -> QuestionDetail:
    """Shared with the write routes (Task 5), which answer with the same
    shape they read — a client that has to re-fetch after a save is a
    client that renders a stale form for one frame."""
    return QuestionDetail(
        id=record.question_id,
        kind=QuestionKind(record.kind),
        prompt=record.prompt,
        category_id=record.category_id,
        category_slug=record.category_slug,
        difficulty=Difficulty(record.difficulty),
        is_active=record.is_active,
        version=record.version,
        media_asset_id=record.media_asset_id,
        choices=(
            [ChoiceView(idx=c.idx, text=c.text, is_correct=c.is_correct,
                        media_asset_id=c.media_asset_id) for c in record.choices]
            if record.choices is not None
            else None
        ),
        numeric_answer=record.numeric_answer,
        unit=record.unit,
    )


@router.get("")
async def list_questions(
    deps: Deps,
    principal: AdminPrincipal,
    kind: QuestionKind | None = None,
    category_id: str | None = None,
    difficulty: Difficulty | None = None,
    is_active: bool | None = None,
    has_media: bool | None = None,
    q: Annotated[str | None, Query(max_length=200)] = None,
    limit: Annotated[int, Query(ge=1, le=MAX_LIMIT)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> QuestionPageView:
    page = await deps.questions_admin.list(
        QuestionFilters(
            kind=None if kind is None else kind.value,
            category_id=category_id,
            difficulty=None if difficulty is None else difficulty.value,
            is_active=is_active,
            has_media=has_media,
            search=q,
        ),
        limit=limit,
        offset=offset,
    )
    return QuestionPageView(
        items=[_summary(item) for item in page.items],
        total=page.total,
        limit=limit,
        offset=offset,
    )


@router.get("/{question_id}")
async def get_question(question_id: str, deps: Deps, principal: AdminPrincipal) -> QuestionDetail:
    record = await deps.questions_admin.get(question_id)
    if record is None:
        raise ApiError(ApiErrorCode.NOT_FOUND, 404, "no such question")
    return detail(record)
```

Wire `questions_admin: QuestionAdminPort` into `AppDependencies` (`unusable` in `placeholder()`, `QuestionAdminRepository(sessions)` in `build_dependencies`) and include the router: `router = build_admin_router(media.router, questions.router)`.

- [ ] **Step 10: Run everything and commit**

Run: `cd backend && uv run pytest -q && uv run mypy && uv run ruff check .`

```bash
git add backend/src/triviador backend/tests
git commit -m "feat(admin): question list with filters and a language-independent prompt search"
```

---

## Task 5: The write paths, and the version bump the pool draw's lock depends on

This is the task Plan 3 pointed at. `db/repositories/questions.py`'s docstring says its `FOR SHARE` on the parent row is sufficient **only because** every semantic edit bumps `questions.version` — and that "enforcing that bump, and testing that an admin path can't skip it, belongs to Plan 7". Here it is.

**Files:**
- Modify: `backend/src/triviador/db/repositories/question_admin.py`, `backend/src/triviador/services/admin.py`
- Modify: `backend/src/triviador/api/schemas/admin/questions.py`, `backend/src/triviador/api/http/admin/questions.py`
- Modify: `backend/src/triviador/api/errors.py` (no new code needed here; `validation_failed` covers the form)
- Test: `backend/tests/db/test_question_admin.py` (append), `backend/tests/api/test_admin_questions.py` (append)

**Interfaces:**
- Produces:
  - `services.admin.QuestionWrite(kind, prompt, category_id, difficulty, media_asset_id, choices, numeric_answer, unit)`
  - `QuestionAdminPort.create(write) -> QuestionDetailRecord`
  - `QuestionAdminPort.update(question_id, write) -> QuestionDetailRecord | None`
  - `QuestionAdminPort.set_active(question_id, *, is_active) -> QuestionDetailRecord | None`
  - `QuestionAdminPort.duplicates_of(prompt, *, excluding) -> tuple[str, ...]`
  - `POST /api/admin/questions` → 201 `QuestionSaved{question, duplicate_of}`; `PATCH /api/admin/questions/{id}` → 200 same
  - `POST /api/admin/questions/{id}/deactivate` and `POST /api/admin/questions/{id}/activate` → 200 `QuestionDetail` (see Decision 8)

- [ ] **Step 1: Write the failing lock test — the important one**

Append to `backend/tests/db/test_question_admin.py`:

```python
async def test_editing_a_choice_bumps_the_parent_version(sessions, clean_db) -> None:
    """The invariant `QuestionBank`'s `FOR SHARE` rests on: a choice lives
    in `question_choices`, which the draw never locks, so an edit that did
    not touch `questions` would be invisible to the lock entirely."""
    await _bank(sessions)
    repository = QuestionAdminRepository(sessions)
    before = await repository.get("q-mc")
    assert before is not None
    await repository.update(
        "q-mc",
        QuestionWrite(
            kind="multiple_choice",
            prompt=before.prompt,
            category_id=before.category_id,
            difficulty=before.difficulty,
            media_asset_id=None,
            choices=(("A", False), ("B", False), ("C", True), ("D", False)),
            numeric_answer=None,
            unit=None,
        ),
    )
    after = await repository.get("q-mc")
    assert after is not None
    assert after.version == before.version + 1
    assert [(c.text, c.is_correct) for c in after.choices or ()] == [
        ("A", False), ("B", False), ("C", True), ("D", False)
    ]


async def test_deactivation_does_not_bump_the_version(sessions, clean_db) -> None:
    """Spec 1 §7: `is_active` is not a semantic edit, and bumping here
    would make Spec 2 treat one question's statistics as two questions'."""
    await _bank(sessions)
    repository = QuestionAdminRepository(sessions)
    before = await repository.get("q-mc")
    await repository.set_active("q-mc", is_active=False)
    after = await repository.get("q-mc")
    assert after is not None and before is not None
    assert (after.is_active, after.version) == (False, before.version)


async def test_an_edit_cannot_slip_past_a_pool_draw_in_flight(engine, sessions, clean_db) -> None:
    """Two transactions, one row.

    A draws the question under `FOR SHARE` — what `QuestionBank` does
    inside `StartGame`'s transaction. B then edits the same question. B
    must block until A commits, because the edit bumps `version`, which is
    an `UPDATE` on the locked row. If this test ever passes instantly, the
    write path has stopped touching `questions` and the lock protects
    nothing.
    """
    import asyncio

    from sqlalchemy import text

    await _bank(sessions)
    repository = QuestionAdminRepository(sessions)
    write = QuestionWrite(
        kind="multiple_choice",
        prompt="Edited while the pool was being drawn",
        category_id="cat-1",
        difficulty="easy",
        media_asset_id=None,
        choices=(("A", True), ("B", False), ("C", False), ("D", False)),
        numeric_answer=None,
        unit=None,
    )

    async with sessions() as drawing:
        async with drawing.begin():
            await drawing.execute(
                text("SELECT id FROM questions WHERE id = :id FOR SHARE"), {"id": "q-mc"}
            )
            with pytest.raises(TimeoutError):
                await asyncio.wait_for(repository.update("q-mc", write), timeout=1.0)
        # The share lock is released by the COMMIT above; the same edit now
        # completes, proving the timeout was the lock and not a deadlock or
        # a broken statement.
        updated = await asyncio.wait_for(repository.update("q-mc", write), timeout=5.0)
    assert updated is not None and updated.prompt == write.prompt


async def test_a_multiple_choice_question_needs_four_choices_and_one_correct(
    sessions, clean_db
) -> None:
    await _bank(sessions)
    repository = QuestionAdminRepository(sessions)
    with pytest.raises(ValueError, match="four"):
        await repository.create(
            QuestionWrite(
                kind="multiple_choice",
                prompt="Three is not four",
                category_id="cat-1",
                difficulty="easy",
                media_asset_id=None,
                choices=(("A", True), ("B", False), ("C", False)),
                numeric_answer=None,
                unit=None,
            ),
            created_by="admin-1",
        )
```

- [ ] **Step 2: Run it and watch it fail**

Run: `cd backend && uv run pytest tests/db/test_question_admin.py -q`
Expected: FAIL — `ImportError: cannot import name 'QuestionWrite'`.

- [ ] **Step 3: Declare the write shape**

Append to `backend/src/triviador/services/admin.py`:

```python
@dataclass(frozen=True)
class QuestionWrite:
    """One question as an admin submits it, in either kind.

    Four choices, exactly one correct, is fixed rather than configurable —
    Spec 1 §10.2: "a configurable count buys nothing and costs variability
    in the answer grid". The tuple carries `(text, is_correct)` pairs in
    display order; `idx` is the position, not a field an admin sets.
    """

    kind: str
    prompt: str
    category_id: str
    difficulty: str
    media_asset_id: str | None
    choices: tuple[tuple[str, bool], ...] | None
    numeric_answer: Decimal | None
    unit: str | None
```

...and extend `QuestionAdminPort`:

```python
    async def create(self, write: QuestionWrite) -> QuestionDetailRecord:
        """No `created_by`. Spec 1 §7's schema gives `media_assets` a
        creator and deliberately gives `questions` none — a question is
        bank content, not a user's artifact, and Spec 2's analytics read
        its statistics rather than its authorship. Threading an admin id
        in here would be a parameter the row has nowhere to put."""
        ...
    async def update(
        self, question_id: str, write: QuestionWrite
    ) -> QuestionDetailRecord | None: ...
    async def set_active(
        self, question_id: str, *, is_active: bool
    ) -> QuestionDetailRecord | None: ...

    async def duplicates_of(self, prompt: str, *, excluding: str | None = None) -> tuple[str, ...]:
        """§10.2: a duplicate prompt is a warning, not a block —
        legitimately similar phrasings exist. The comparison is
        `prompt_digest`, the same whitespace- and case-insensitive hash
        `seed-questions` already uses."""
        ...

    async def existing_prompt_digests(self, digests: frozenset[str]) -> frozenset[str]:
        """Which of these the bank already has, in one query.

        The import's warning channel (Task 7) asks this once per upload
        rather than calling `duplicates_of` per row — same rule, same
        digest, one round trip.
        """
        ...
```

- [ ] **Step 4: Write the write paths**

Append to `backend/src/triviador/db/repositories/question_admin.py`:

```python
CHOICE_COUNT = 4


def _validate(write: QuestionWrite) -> None:
    """Shape only. The *route* validates types and lengths through Pydantic;
    this is the invariant that must hold no matter who calls — the importer
    (Task 8) reaches these methods without passing through a schema."""
    if write.kind == QuestionKind.MULTIPLE_CHOICE.value:
        choices = write.choices or ()
        if len(choices) != CHOICE_COUNT:
            raise ValueError(f"a multiple-choice question needs exactly four choices")
        if sum(1 for _, correct in choices if correct) != 1:
            raise ValueError("a multiple-choice question needs exactly one correct choice")
        if write.numeric_answer is not None or write.unit is not None:
            raise ValueError("a multiple-choice question carries no numeric answer")
    elif write.kind == QuestionKind.NUMERIC.value:
        if write.numeric_answer is None:
            raise ValueError("a numeric question needs an answer")
        if write.choices:
            raise ValueError("a numeric question carries no choices")
    else:
        raise ValueError(f"unknown question kind {write.kind!r}")


class QuestionAdminRepository:   # ...continues
    async def create(self, write: QuestionWrite) -> QuestionDetailRecord:
        _validate(write)
        question_id = str(uuid4())
        async with self._sessionmaker() as session, session.begin():
            session.add(
                Question(
                    id=question_id,
                    version=1,
                    kind=write.kind,
                    prompt=write.prompt,
                    category_id=write.category_id,
                    difficulty=write.difficulty,
                    media_asset_id=write.media_asset_id,
                    is_active=True,
                    prompt_hash=prompt_digest(write.prompt),
                )
            )
            await session.flush()
            self._write_children(session, question_id, write)
        record = await self.get(question_id)
        assert record is not None  # inserted and committed above
        return record

    async def update(self, question_id: str, write: QuestionWrite) -> QuestionDetailRecord | None:
        """Every call bumps `version`.

        Unconditionally, and without comparing old to new: this method is
        only reachable for a semantic edit (`is_active` has its own
        method), and a "did anything really change?" comparison is exactly
        the optimisation that eventually decides a choice-only edit did
        not count. The bump is also the lock — see the module docstring of
        `repositories/questions.py`.
        """
        _validate(write)
        async with self._sessionmaker() as session, session.begin():
            row = await session.get(Question, question_id, with_for_update=True)
            if row is None:
                return None
            row.kind = write.kind
            row.prompt = write.prompt
            row.prompt_hash = prompt_digest(write.prompt)
            row.category_id = write.category_id
            row.difficulty = write.difficulty
            row.media_asset_id = write.media_asset_id
            row.version = row.version + 1
            await session.execute(
                delete(QuestionChoice).where(QuestionChoice.question_id == question_id)
            )
            await session.execute(
                delete(QuestionNumeric).where(QuestionNumeric.question_id == question_id)
            )
            await session.flush()
            self._write_children(session, question_id, write)
        return await self.get(question_id)

    async def set_active(self, question_id: str, *, is_active: bool) -> QuestionDetailRecord | None:
        """No version bump (Spec 1 §7). The `UPDATE` still takes a row lock
        on `questions`, so a deactivation cannot race a pool draw either —
        that part comes free from touching the parent row."""
        async with self._sessionmaker() as session, session.begin():
            row = await session.get(Question, question_id, with_for_update=True)
            if row is None:
                return None
            row.is_active = is_active
        return await self.get(question_id)

    async def duplicates_of(self, prompt: str, *, excluding: str | None = None) -> tuple[str, ...]:
        digest = prompt_digest(prompt)
        async with self._sessionmaker() as session:
            statement = select(Question.id).where(Question.prompt_hash == digest)
            if excluding is not None:
                statement = statement.where(Question.id != excluding)
            return tuple((await session.execute(statement)).scalars().all())

    async def existing_prompt_digests(self, digests: frozenset[str]) -> frozenset[str]:
        if not digests:
            return frozenset()
        async with self._sessionmaker() as session:
            rows = await session.execute(
                select(Question.prompt_hash).where(Question.prompt_hash.in_(digests))
            )
            return frozenset(rows.scalars().all())

    @staticmethod
    def _write_children(session: AsyncSession, question_id: str, write: QuestionWrite) -> None:
        if write.kind == QuestionKind.NUMERIC.value:
            session.add(
                QuestionNumeric(
                    question_id=question_id,
                    correct_value=write.numeric_answer,
                    unit=write.unit,
                )
            )
            return
        for idx, (text, is_correct) in enumerate(write.choices or ()):
            session.add(
                QuestionChoice(
                    question_id=question_id,
                    idx=idx,
                    text=text,
                    is_correct=is_correct,
                    media_asset_id=None,
                )
            )
```

...with `from uuid import uuid4`, `from sqlalchemy import delete`, `from triviador.db.repositories.questions import prompt_digest`, `from triviador.domain.questions.types import QuestionKind`, and `from triviador.services.admin import QuestionWrite` added to the imports.

Run: `cd backend && uv run pytest tests/db/test_question_admin.py -q` → PASS (all nine).

- [ ] **Step 5: Write the failing route test**

Append to `backend/tests/api/test_admin_questions.py`:

```python
MC_BODY = {
    "kind": "multiple_choice",
    "prompt": "Which river runs through Prague?",
    "category_id": "cat-1",
    "difficulty": "easy",
    "media_asset_id": None,
    "choices": [
        {"text": "Vltava", "is_correct": True},
        {"text": "Elbe", "is_correct": False},
        {"text": "Morava", "is_correct": False},
        {"text": "Ohře", "is_correct": False},
    ],
    "numeric_answer": None,
    "unit": None,
}


async def test_creating_a_question_answers_201_with_the_saved_question(
    admin_client: httpx.AsyncClient
) -> None:
    response = await admin_client.post("/api/admin/questions", json=MC_BODY)
    assert response.status_code == 201
    assert response.json()["question"]["prompt"] == MC_BODY["prompt"]
    assert response.json()["duplicate_of"] == []


async def test_three_choices_is_a_validation_error(admin_client: httpx.AsyncClient) -> None:
    body = {**MC_BODY, "choices": MC_BODY["choices"][:3]}
    response = await admin_client.post("/api/admin/questions", json=body)
    assert response.status_code == 422
    assert response.json()["code"] == "validation_failed"


async def test_two_correct_choices_is_a_validation_error(admin_client: httpx.AsyncClient) -> None:
    choices = [dict(c) for c in MC_BODY["choices"]]
    choices[1]["is_correct"] = True
    response = await admin_client.post("/api/admin/questions", json={**MC_BODY, "choices": choices})
    assert response.status_code == 422


async def test_a_duplicate_prompt_is_a_warning_and_still_saves(
    admin_client: httpx.AsyncClient
) -> None:
    """§10.2: legitimately similar phrasings exist, so the duplicate hash
    surfaces as a field on a 201, never as a 409."""
    first = await admin_client.post("/api/admin/questions", json=MC_BODY)
    second = await admin_client.post(
        "/api/admin/questions", json={**MC_BODY, "prompt": "  which river RUNS through prague? "}
    )
    assert second.status_code == 201
    assert second.json()["duplicate_of"] == [first.json()["question"]["id"]]


async def test_patching_a_missing_question_is_404(admin_client: httpx.AsyncClient) -> None:
    assert (await admin_client.patch("/api/admin/questions/nope", json=MC_BODY)).status_code == 404


async def test_deactivate_and_activate_flip_the_flag_without_bumping_version(
    admin_client: httpx.AsyncClient
) -> None:
    """Both directions, because §10.2 puts `is_active` in the editor and a
    bank whose rows can never be deleted (§7) needs retirement to be
    reversible. Neither touches `version` — Spec 1 §7 again."""
    created = (await admin_client.post("/api/admin/questions", json=MC_BODY)).json()["question"]
    off = await admin_client.post(f"/api/admin/questions/{created['id']}/deactivate")
    assert off.status_code == 200
    assert (off.json()["is_active"], off.json()["version"]) == (False, created["version"])
    on = await admin_client.post(f"/api/admin/questions/{created['id']}/activate")
    assert (on.json()["is_active"], on.json()["version"]) == (True, created["version"])
```

Extend `FakeQuestionAdmin` in `backend/tests/api/fakes.py` with `create`, `update`, `set_active` and `duplicates_of` over its dict, reusing `prompt_digest` for the duplicate check so the fake and the repository agree on what "duplicate" means.

- [ ] **Step 6: Write the request schemas and the routes**

Append to `backend/src/triviador/api/schemas/admin/questions.py`:

```python
class ChoiceWrite(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str = Field(min_length=1, max_length=200)
    is_correct: bool


class QuestionWriteRequest(BaseModel):
    """Both kinds in one body, validated as a whole.

    A discriminated union of two request models would be tidier on paper
    and worse here: the editor is one form whose fields appear and
    disappear, and a client posting `{kind: "numeric", choices: []}`
    deserves the field-level error this shape gives it rather than "no
    variant matched".
    """

    model_config = ConfigDict(extra="forbid")

    kind: QuestionKind
    prompt: str = Field(min_length=1, max_length=1000)
    category_id: str
    difficulty: Difficulty
    media_asset_id: str | None = None
    choices: list[ChoiceWrite] | None = None
    numeric_answer: Decimal | None = None
    unit: str | None = Field(default=None, max_length=16)

    @model_validator(mode="after")
    def _shape(self) -> "QuestionWriteRequest":
        if self.kind is QuestionKind.MULTIPLE_CHOICE:
            if self.choices is None or len(self.choices) != 4:
                raise ValueError("a multiple-choice question needs exactly four choices")
            if sum(1 for c in self.choices if c.is_correct) != 1:
                raise ValueError("a multiple-choice question needs exactly one correct choice")
            if self.numeric_answer is not None or self.unit is not None:
                raise ValueError("a multiple-choice question carries no numeric answer")
        else:
            if self.numeric_answer is None:
                raise ValueError("a numeric question needs an answer")
            if not self.numeric_answer.is_finite():
                raise ValueError("a numeric answer must be finite")
            if self.choices:
                raise ValueError("a numeric question carries no choices")
        return self


class QuestionSaved(BaseModel):
    """The saved question, plus §10.2's duplicate *warning*.

    One response rather than a 409 and a retry: the admin has already
    written the question, and the only useful thing to do with the
    similarity is show it beside what they saved.
    """

    model_config = ConfigDict(extra="forbid")

    question: QuestionDetail
    duplicate_of: list[str]
```

...with `Field` and `model_validator` imported from pydantic.

Append the routes to `backend/src/triviador/api/http/admin/questions.py`:

```python
def _write(body: QuestionWriteRequest) -> QuestionWrite:
    return QuestionWrite(
        kind=body.kind.value,
        prompt=body.prompt.strip(),
        category_id=body.category_id,
        difficulty=body.difficulty.value,
        media_asset_id=body.media_asset_id,
        choices=(
            tuple((c.text, c.is_correct) for c in body.choices)
            if body.choices is not None
            else None
        ),
        numeric_answer=body.numeric_answer,
        unit=body.unit,
    )


@router.post("", status_code=201)
async def create_question(
    body: QuestionWriteRequest, deps: Deps, principal: AdminPrincipal
) -> QuestionSaved:
    record = await deps.questions_admin.create(_write(body))
    duplicates = await deps.questions_admin.duplicates_of(
        body.prompt, excluding=record.question_id
    )
    return QuestionSaved(question=detail(record), duplicate_of=list(duplicates))


@router.patch("/{question_id}")
async def update_question(
    question_id: str, body: QuestionWriteRequest, deps: Deps, principal: AdminPrincipal
) -> QuestionSaved:
    record = await deps.questions_admin.update(question_id, _write(body))
    if record is None:
        raise ApiError(ApiErrorCode.NOT_FOUND, 404, "no such question")
    duplicates = await deps.questions_admin.duplicates_of(body.prompt, excluding=question_id)
    return QuestionSaved(question=detail(record), duplicate_of=list(duplicates))


@router.post("/{question_id}/deactivate")
async def deactivate_question(
    question_id: str, deps: Deps, principal: AdminPrincipal
) -> QuestionDetail:
    record = await deps.questions_admin.set_active(question_id, is_active=False)
    if record is None:
        raise ApiError(ApiErrorCode.NOT_FOUND, 404, "no such question")
    return detail(record)


@router.post("/{question_id}/activate")
async def activate_question(
    question_id: str, deps: Deps, principal: AdminPrincipal
) -> QuestionDetail:
    """The route Spec 1B §6.1 does not list, and Spec 1 §10.2 requires.

    §10.2 puts `is_active` in the editor's common fields, so an admin must
    be able to set it in both directions; §6.1 lists only `deactivate`.
    Taken literally, retiring a question by mistake would be permanent —
    for a bank whose rows can never be deleted (§7).

    It is a route rather than a field on `PATCH` so that activity stays
    outside the semantic-edit path: `PATCH` always bumps
    `questions.version` (it rewrites prompt, choices and answer), and
    Spec 1 §7 says toggling `is_active` must *not* bump it, or Spec 2
    would read one question's statistics as two questions'. Two routes
    keep both rules true without a comparison deciding which applies.
    """
    record = await deps.questions_admin.set_active(question_id, is_active=True)
    if record is None:
        raise ApiError(ApiErrorCode.NOT_FOUND, 404, "no such question")
    return detail(record)
```

- [ ] **Step 7: Run everything and commit**

Run: `cd backend && uv run pytest -q && uv run mypy && uv run ruff check .`

```bash
git add backend/src/triviador backend/tests
git commit -m "feat(admin): question create/edit/deactivate, with the version bump the draw lock needs"
```

---

## Task 6: Categories

Small, and needed by everything above it: a question cannot be created without a `category_id`, and the editor's category select has nothing to show until this exists.

**Files:**
- Create: `backend/src/triviador/db/repositories/categories.py`, `backend/src/triviador/api/schemas/admin/categories.py`, `backend/src/triviador/api/http/admin/categories.py`
- Modify: `backend/src/triviador/services/admin.py`, `backend/src/triviador/api/errors.py` (`SLUG_TAKEN`), `.../http/admin/__init__.py`, `deps.py`, `app.py`, `tests/api/conftest.py`, `tests/api/fakes.py`
- Test: `backend/tests/api/test_admin_categories.py`, `backend/tests/db/test_admin_repositories.py`

**Interfaces:**
- Produces: `services.admin.CategoryRecord(category_id, slug, name)`, `CategoryPort.list()/create(slug, name)/rename(category_id, name)`; `GET /api/admin/categories`, `POST /api/admin/categories`, `PATCH /api/admin/categories/{id}`; `ApiErrorCode.SLUG_TAKEN`

- [ ] **Step 1: Write the failing tests**

Create `backend/tests/api/test_admin_categories.py`:

```python
import httpx
import pytest

pytestmark = pytest.mark.asyncio


async def test_a_player_cannot_list_categories(signed_in: httpx.AsyncClient) -> None:
    assert (await signed_in.get("/api/admin/categories")).status_code == 403


async def test_create_then_list(admin_client: httpx.AsyncClient) -> None:
    created = await admin_client.post(
        "/api/admin/categories", json={"slug": "geography", "name": "Geography"}
    )
    assert created.status_code == 201
    listed = await admin_client.get("/api/admin/categories")
    assert {c["slug"] for c in listed.json()} >= {"geography"}


async def test_a_duplicate_slug_is_409_not_500(admin_client: httpx.AsyncClient) -> None:
    """`categories.slug` is UNIQUE. Without a deliberate check the second
    create surfaces as `IntegrityError` → 503 `database_unavailable`,
    which tells the admin the database is down when their input was
    simply already there."""
    body = {"slug": "film", "name": "Film"}
    assert (await admin_client.post("/api/admin/categories", json=body)).status_code == 201
    duplicate = await admin_client.post("/api/admin/categories", json=body)
    assert duplicate.status_code == 409
    assert duplicate.json()["code"] == "slug_taken"


async def test_a_slug_is_lowercase_and_dashed(admin_client: httpx.AsyncClient) -> None:
    response = await admin_client.post(
        "/api/admin/categories", json={"slug": "Pop Music", "name": "Pop"}
    )
    assert response.status_code == 422


async def test_rename_keeps_the_slug(admin_client: httpx.AsyncClient) -> None:
    """The slug is an identifier the seed CSV and the import format both
    reference by value (`category_slug`); renaming the display name must
    not silently repoint every future import."""
    created = (
        await admin_client.post("/api/admin/categories", json={"slug": "sport", "name": "Sport"})
    ).json()
    renamed = await admin_client.patch(
        f"/api/admin/categories/{created['id']}", json={"name": "Sports"}
    )
    assert renamed.status_code == 200
    assert renamed.json() == {"id": created["id"], "slug": "sport", "name": "Sports"}
```

- [ ] **Step 2: Run it and watch it fail**

Run: `cd backend && uv run pytest tests/api/test_admin_categories.py -q`
Expected: FAIL — 404 on `/api/admin/categories`.

- [ ] **Step 3: Port, repository, schema, route**

Append to `backend/src/triviador/services/admin.py`:

```python
@dataclass(frozen=True)
class CategoryRecord:
    category_id: str
    slug: str
    name: str


class SlugTaken(Exception):
    """A category with that slug exists. Raised by the repository rather
    than reported as a bool, because it is the *only* failure `create` has
    and a bool return would put the burden of remembering that on every
    caller."""


class CategoryPort(Protocol):
    async def list(self) -> tuple[CategoryRecord, ...]: ...
    async def create(self, *, slug: str, name: str) -> CategoryRecord: ...
    async def rename(self, category_id: str, *, name: str) -> CategoryRecord | None: ...
```

Create `backend/src/triviador/db/repositories/categories.py`:

```python
"""`categories`, which nothing else may write.

`QuestionSeeder.ensure_category` (Plan 6) also inserts categories, and
deliberately stays where it is: it is idempotent seeding, not an admin
write path, and it never renames. Both go through the same UNIQUE
constraint, which is what keeps them honest.
"""

from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from triviador.db.models.content import Category
from triviador.services.admin import CategoryRecord, SlugTaken


class CategoryRepository:
    """Implements `services.admin.CategoryPort`."""

    def __init__(self, sessionmaker: async_sessionmaker[AsyncSession]) -> None:
        self._sessionmaker = sessionmaker

    async def list(self) -> tuple[CategoryRecord, ...]:
        async with self._sessionmaker() as session:
            rows = (
                await session.execute(select(Category).order_by(Category.slug))
            ).scalars().all()
        return tuple(CategoryRecord(r.id, r.slug, r.name) for r in rows)

    async def create(self, *, slug: str, name: str) -> CategoryRecord:
        category = Category(id=str(uuid4()), slug=slug, name=name)
        try:
            async with self._sessionmaker() as session, session.begin():
                session.add(category)
        except IntegrityError as exc:
            # `categories.slug` is the only UNIQUE constraint on this
            # table, so this cannot mean anything else.
            raise SlugTaken(slug) from exc
        return CategoryRecord(category.id, slug, name)

    async def rename(self, category_id: str, *, name: str) -> CategoryRecord | None:
        async with self._sessionmaker() as session, session.begin():
            row = await session.get(Category, category_id)
            if row is None:
                return None
            row.name = name
            return CategoryRecord(row.id, row.slug, name)
```

Create `backend/src/triviador/api/schemas/admin/categories.py`:

```python
from pydantic import BaseModel, ConfigDict, Field


class CategoryView(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    slug: str
    name: str


class CreateCategoryRequest(BaseModel):
    """The slug pattern is the same shape the seed CSV's `category_slug`
    column uses, so a category created here can be referenced by a later
    import without transformation."""

    model_config = ConfigDict(extra="forbid")

    slug: str = Field(min_length=1, max_length=48, pattern=r"^[a-z0-9]+(-[a-z0-9]+)*$")
    name: str = Field(min_length=1, max_length=64)


class RenameCategoryRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=64)
```

Create `backend/src/triviador/api/http/admin/categories.py`:

```python
from fastapi import APIRouter

from triviador.api.deps import AdminPrincipal, Deps
from triviador.api.errors import ApiError, ApiErrorCode
from triviador.api.schemas.admin.categories import (
    CategoryView,
    CreateCategoryRequest,
    RenameCategoryRequest,
)
from triviador.services.admin import CategoryRecord, SlugTaken

router = APIRouter(prefix="/categories", tags=["admin"])


def _view(record: CategoryRecord) -> CategoryView:
    return CategoryView(id=record.category_id, slug=record.slug, name=record.name)


@router.get("")
async def list_categories(deps: Deps, principal: AdminPrincipal) -> list[CategoryView]:
    return [_view(record) for record in await deps.categories.list()]


@router.post("", status_code=201)
async def create_category(
    body: CreateCategoryRequest, deps: Deps, principal: AdminPrincipal
) -> CategoryView:
    try:
        return _view(await deps.categories.create(slug=body.slug, name=body.name))
    except SlugTaken as exc:
        raise ApiError(
            ApiErrorCode.SLUG_TAKEN, 409, f"a category with slug {body.slug!r} already exists"
        ) from exc


@router.patch("/{category_id}")
async def rename_category(
    category_id: str, body: RenameCategoryRequest, deps: Deps, principal: AdminPrincipal
) -> CategoryView:
    record = await deps.categories.rename(category_id, name=body.name)
    if record is None:
        raise ApiError(ApiErrorCode.NOT_FOUND, 404, "no such category")
    return _view(record)
```

Add `SLUG_TAKEN = "slug_taken"` to `ApiErrorCode`, wire `categories: CategoryPort` into `AppDependencies` / `build_dependencies` / the `deps` fixture (with a `FakeCategories` dict-backed fake), and include the router in `build_admin_router(...)`.

Add the repository half to `backend/tests/db/test_admin_repositories.py`:

```python
import pytest

from triviador.db.repositories.categories import CategoryRepository
from triviador.services.admin import SlugTaken

pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="session")]


async def test_a_duplicate_slug_raises_slug_taken(sessions, clean_db) -> None:
    repository = CategoryRepository(sessions)
    await repository.create(slug="film", name="Film")
    with pytest.raises(SlugTaken):
        await repository.create(slug="film", name="Cinema")


async def test_rename_leaves_the_slug_alone(sessions, clean_db) -> None:
    repository = CategoryRepository(sessions)
    created = await repository.create(slug="sport", name="Sport")
    renamed = await repository.rename(created.category_id, name="Sports")
    assert renamed == CategoryRecord(created.category_id, "sport", "Sports")
```

- [ ] **Step 4: Run everything and commit**

Run: `cd backend && uv run pytest -q && uv run mypy && uv run ruff check .`

```bash
git add backend/src/triviador backend/tests
git commit -m "feat(admin): category list, create and rename"
```

---

## Task 7: The import, phase one — validate everything, write nothing

§10.3 and §9.3's dry-run invariant, stated so it can be tested: *dry-run persists staging metadata and the original upload, and writes no categories, questions, choices, numeric answers, media assets, or public media objects.*

**Files:**
- Create: `backend/src/triviador/imports/__init__.py`, `backend/src/triviador/imports/parse.py`
- Create: `backend/src/triviador/db/repositories/imports.py`
- Create: `backend/src/triviador/api/schemas/admin/imports.py`, `backend/src/triviador/api/http/admin/imports.py`
- Modify: `backend/src/triviador/services/admin.py`, `deps.py`, `app.py`, `http/admin/__init__.py`, `tests/api/conftest.py`, `tests/api/fakes.py`
- Modify: `backend/src/triviador/api/errors.py` (`IMPORT_NOT_CONFIRMABLE`)
- Test: `backend/tests/imports/__init__.py`, `backend/tests/imports/test_parse.py` (pure)
- Test: `backend/tests/api/test_admin_imports.py`

**Interfaces:**
- Produces:
  - `imports.parse.parse_upload(data: bytes, *, filename: str) -> ParsedImport`
  - `imports.parse.ParsedImport(rows, rejections, media)`, `ParsedRow`, `Rejection`, `UploadRejected`
  - `services.admin.ImportRecord(import_id, uploaded_by, upload_sha256, filename, staged_key, row_count, rejected_count, report, status, expires_at)`
  - `services.admin.ImportPort.create(...)`, `.get(import_id)`
  - `POST /api/admin/questions/import/dry-run` → 201 `ImportSummary`
  - `GET /api/admin/questions/import/{id}/rejected.csv` → `text/csv`

- [ ] **Step 1: Write the failing parser test**

Create `backend/tests/imports/__init__.py` (empty) and `backend/tests/imports/test_parse.py`:

```python
"""Pure. No database, no bucket, no event loop — the format is the whole
subject, and every rejection here is a line an admin has to fix in a
spreadsheet, so each one names its line number.
"""

import io
import zipfile
from decimal import Decimal

import pytest

from triviador.imports.parse import UploadRejected, parse_upload

HEADER = (
    "kind,prompt,category,difficulty,"
    "choice_1,choice_2,choice_3,choice_4,correct_index,numeric_answer,unit,media_file"
)
MC = "multiple_choice,Which river runs through Prague?,geography,easy,Vltava,Elbe,Morava,Ohře,0,,,"
NUM = "numeric,In which year did the Velvet Revolution begin?,history,easy,,,,,,1989,,"


def csv_bytes(*lines: str) -> bytes:
    return "\n".join((HEADER, *lines)).encode("utf-8")


def zip_bytes(csv: bytes, media: dict[str, bytes]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("questions.csv", csv)
        for name, blob in media.items():
            archive.writestr(f"media/{name}", blob)
    return buffer.getvalue()


def test_a_plain_csv_parses_both_kinds() -> None:
    parsed = parse_upload(csv_bytes(MC, NUM), filename="bank.csv")
    assert parsed.rejections == ()
    assert [r.kind for r in parsed.rows] == ["multiple_choice", "numeric"]
    assert parsed.rows[0].choices == (
        ("Vltava", True), ("Elbe", False), ("Morava", False), ("Ohře", False)
    )
    assert parsed.rows[1].numeric_answer == Decimal("1989")


def test_a_bad_row_is_rejected_by_line_number_and_the_rest_survive() -> None:
    """Row-level, not file-level: the admin gets a rejected-rows CSV to fix
    and re-upload, which is only useful if the good rows were accepted in
    the report."""
    parsed = parse_upload(csv_bytes(MC, "numeric,No answer here,history,easy,,,,,,,,"), "b.csv")
    assert [r.line for r in parsed.rejections] == [3]
    assert "answer" in parsed.rejections[0].reason
    assert len(parsed.rows) == 1


@pytest.mark.parametrize(
    ("row", "reason"),
    [
        ("multiple_choice,Three choices,geography,easy,A,B,C,,0,,,", "four"),
        ("multiple_choice,Bad index,geography,easy,A,B,C,D,9,,,", "correct_index"),
        ("multiple_choice,With answer,geography,easy,A,B,C,D,0,12,,", "numeric"),
        ("numeric,With choices,history,easy,A,B,C,D,,1989,,", "choices"),
        ("numeric,Not a number,history,easy,,,,,,twelve,,", "decimal"),
        ("picture,Unknown kind,history,easy,,,,,,1,,", "kind"),
        ("numeric,Unknown difficulty,history,trivial,,,,,,1,,", "difficulty"),
        ("numeric,,history,easy,,,,,,1,,", "prompt"),
    ],
)
def test_each_row_level_rule(row: str, reason: str) -> None:
    parsed = parse_upload(csv_bytes(row), filename="b.csv")
    assert parsed.rows == ()
    assert reason in parsed.rejections[0].reason


def test_a_duplicate_prompt_inside_one_file_is_a_warning_not_a_rejection() -> None:
    """§10.2 is unambiguous: a prompt-digest match "surfaces a warning,
    not a block", on save *and on import*. Rejecting here would also make
    the upload unconfirmable (§10.3 gates confirm on `rejected == 0`), so
    a file with one accidental repeat could never be applied at all —
    which is a block wearing a warning's name."""
    parsed = parse_upload(csv_bytes(NUM, NUM), filename="b.csv")
    assert len(parsed.rows) == 2
    assert parsed.rejections == ()
    assert [n.line for n in parsed.notices] == [3]
    assert "duplicate" in parsed.notices[0].reason


def test_a_wrong_header_is_a_whole_upload_rejection() -> None:
    """Not a row rejection: a file with the wrong columns has no rows to
    report on, and "1000 rejected rows" would bury the one fact that
    matters."""
    with pytest.raises(UploadRejected, match="header"):
        parse_upload(b"a,b,c\n1,2,3", filename="b.csv")


def test_a_zip_carries_its_media() -> None:
    parsed = parse_upload(zip_bytes(csv_bytes(MC.replace(",,,", ",,,river.png")),
                                    {"river.png": b"PNGDATA"}), filename="bank.zip")
    assert parsed.rows[0].media_file == "river.png"
    assert parsed.media["river.png"] == b"PNGDATA"


def test_a_row_naming_a_missing_media_file_is_rejected() -> None:
    parsed = parse_upload(zip_bytes(csv_bytes(MC.replace(",,,", ",,,absent.png")), {}), "b.zip")
    assert parsed.rows == ()
    assert "absent.png" in parsed.rejections[0].reason


def test_a_csv_may_not_reference_media_at_all() -> None:
    """§10.3: "Plain `.csv` is accepted without images." A media reference
    with no archive to hold it is a mistake worth naming."""
    parsed = parse_upload(csv_bytes(MC.replace(",,,", ",,,river.png")), filename="b.csv")
    assert "media" in parsed.rejections[0].reason


def test_a_zip_without_questions_csv_is_rejected() -> None:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("other.csv", csv_bytes(MC))
    with pytest.raises(UploadRejected, match="questions.csv"):
        parse_upload(buffer.getvalue(), filename="b.zip")


def test_a_traversal_path_in_the_archive_is_refused() -> None:
    """`media/../../etc/passwd` never reaches a filesystem here — nothing
    is extracted to disk — but a name like that is either an attack or a
    corrupt archive, and treating it as an ordinary key would put it in an
    anonymously readable bucket."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("questions.csv", csv_bytes(MC))
        archive.writestr("media/../../escape.png", b"x")
    with pytest.raises(UploadRejected, match="path"):
        parse_upload(buffer.getvalue(), filename="b.zip")


def test_an_archive_that_expands_absurdly_is_refused() -> None:
    """A zip bomb: 40 MB of zeroes compresses to a few kilobytes, and this
    parser holds what it reads in memory."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("questions.csv", csv_bytes(MC))
        archive.writestr("media/huge.png", b"\0" * (200 * 1024 * 1024))
    with pytest.raises(UploadRejected, match="expands"):
        parse_upload(buffer.getvalue(), filename="b.zip")
```

- [ ] **Step 2: Run it and watch it fail**

Run: `cd backend && uv run pytest tests/imports -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'triviador.imports'`.

- [ ] **Step 3: Write the parser**

Create `backend/src/triviador/imports/__init__.py`:

```python
"""The two-phase question import (§10.3, §9.3).

`parse.py` is pure and knows nothing about storage; `retire.py` (Task 9)
owns the expiry state machine. The orchestration — read, validate, stage,
confirm — lives in `api/http/admin/imports.py`, where the transaction
boundary and the request are.
"""
```

Create `backend/src/triviador/imports/parse.py`:

```python
"""`.csv` or `.zip` in, rows and rejections out. Nothing is written here.

**Row rejections versus upload rejections.** A row that fails its own
rules is reported by line number and the rest of the file is still
parsed — §10.3's workflow is "download the rejected rows, fix them,
repeat", which needs the good rows counted. A file whose *header* is
wrong, or whose archive has no `questions.csv`, has no rows to report at
all, so it raises `UploadRejected` and the request fails as a whole.

**Everything is held in memory, deliberately.** The upload is capped at
`import_max_bytes` by the route, the archive is capped again here by
expanded size, and the alternative — spooling to a temp file — would put
answer keys on the application container's disk, which nothing else in
this system does.
"""

import csv
import io
import zipfile
from collections.abc import Mapping
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation

from triviador.db.repositories.questions import prompt_digest  # noqa: F401  (see below)
from triviador.domain.questions.types import Difficulty, QuestionKind

COLUMNS = (
    "kind",
    "prompt",
    "category",
    "difficulty",
    "choice_1",
    "choice_2",
    "choice_3",
    "choice_4",
    "correct_index",
    "numeric_answer",
    "unit",
    "media_file",
)

CHOICE_COUNT = 4
MAX_EXPANDED_BYTES = 128 * 1024 * 1024


class UploadRejected(Exception):
    """The upload as a whole is unusable; there is nothing to report per row."""


@dataclass(frozen=True)
class ParsedRow:
    line: int
    kind: str
    prompt: str
    category_slug: str
    difficulty: str
    choices: tuple[tuple[str, bool], ...] | None
    numeric_answer: Decimal | None
    unit: str | None
    media_file: str | None
    raw: Mapping[str, str]


@dataclass(frozen=True)
class Rejection:
    line: int
    reason: str
    raw: Mapping[str, str]


@dataclass(frozen=True)
class Notice:
    """Something the admin should see and may ignore.

    Separate from `Rejection` because the two have opposite consequences:
    a rejection makes the upload unconfirmable (§10.3), a notice does not.
    Collapsing them is how §10.2's "warning, not a block" quietly becomes
    a block.
    """

    line: int
    reason: str


@dataclass(frozen=True)
class ParsedImport:
    rows: tuple[ParsedRow, ...] = ()
    rejections: tuple[Rejection, ...] = ()
    notices: tuple[Notice, ...] = ()
    media: Mapping[str, bytes] = field(default_factory=dict)


def parse_upload(data: bytes, *, filename: str) -> ParsedImport:
    if filename.lower().endswith(".zip"):
        text, media = _open_archive(data)
    else:
        text, media = data.decode("utf-8-sig", errors="replace"), {}
    return _parse_rows(text, media, archive=filename.lower().endswith(".zip"))


def _open_archive(data: bytes) -> tuple[str, dict[str, bytes]]:
    try:
        archive = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile as exc:
        raise UploadRejected("that file is not a readable .zip archive") from exc

    with archive:
        names = archive.namelist()
        if any(name.startswith("/") or ".." in name.split("/") for name in names):
            raise UploadRejected("the archive contains an unsafe path")
        if sum(info.file_size for info in archive.infolist()) > MAX_EXPANDED_BYTES:
            raise UploadRejected(
                f"the archive expands to more than {MAX_EXPANDED_BYTES} bytes"
            )
        if "questions.csv" not in names:
            raise UploadRejected("the archive must contain questions.csv")
        text = archive.read("questions.csv").decode("utf-8-sig", errors="replace")
        media = {
            name.removeprefix("media/"): archive.read(name)
            for name in names
            if name.startswith("media/") and not name.endswith("/")
        }
    return text, media


def _parse_rows(text: str, media: Mapping[str, bytes], *, archive: bool) -> ParsedImport:
    reader = csv.DictReader(io.StringIO(text))
    if tuple(reader.fieldnames or ()) != COLUMNS:
        raise UploadRejected(f"header must be exactly {','.join(COLUMNS)}")

    rows: list[ParsedRow] = []
    rejections: list[Rejection] = []
    notices: list[Notice] = []
    seen: dict[str, int] = {}
    for line, raw in enumerate(reader, start=2):
        try:
            row = _parse_row(line, raw, media, archive=archive)
        except ValueError as exc:
            rejections.append(Rejection(line=line, reason=str(exc), raw=raw))
            continue
        digest = prompt_digest(row.prompt)
        if digest in seen:
            # A notice, and the row is still imported: §10.2 says a digest
            # match is a warning on save *and on import*, and legitimately
            # similar phrasings normalise to the same digest.
            notices.append(
                Notice(line=line, reason=f"same prompt as line {seen[digest]} of this upload")
            )
        seen.setdefault(digest, line)
        rows.append(row)
    return ParsedImport(
        rows=tuple(rows),
        rejections=tuple(rejections),
        notices=tuple(notices),
        media=media,
    )


def _parse_row(
    line: int, raw: Mapping[str, str], media: Mapping[str, bytes], *, archive: bool
) -> ParsedRow:
    def cell(name: str) -> str:
        return (raw.get(name) or "").strip()

    kind = cell("kind")
    if kind not in {k.value for k in QuestionKind}:
        raise ValueError(f"unknown kind {kind!r}")
    difficulty = cell("difficulty")
    if difficulty not in {d.value for d in Difficulty}:
        raise ValueError(f"unknown difficulty {difficulty!r}")
    prompt = cell("prompt")
    if not prompt:
        raise ValueError("empty prompt")
    category = cell("category")
    if not category:
        raise ValueError("empty category")

    media_file = cell("media_file") or None
    if media_file is not None:
        if not archive:
            raise ValueError("a plain .csv cannot reference media; upload a .zip instead")
        if media_file not in media:
            raise ValueError(f"media file {media_file!r} is not in the archive")

    choices = tuple(cell(f"choice_{i}") for i in (1, 2, 3, 4))
    answer = cell("numeric_answer")
    unit = cell("unit") or None
    index_raw = cell("correct_index")

    if kind == QuestionKind.NUMERIC.value:
        if any(choices) or index_raw:
            raise ValueError("a numeric question carries no choices")
        if not answer:
            raise ValueError("a numeric question needs a numeric_answer")
        try:
            value = Decimal(answer)
        except InvalidOperation as exc:
            raise ValueError(f"numeric_answer {answer!r} is not a decimal number") from exc
        if not value.is_finite():
            raise ValueError("numeric_answer must be finite")
        return ParsedRow(line, kind, prompt, category, difficulty, None, value, unit,
                         media_file, raw)

    if answer or unit:
        raise ValueError("a multiple-choice question carries no numeric_answer or unit")
    if sum(1 for c in choices if c) != CHOICE_COUNT:
        raise ValueError("a multiple-choice question needs exactly four choices")
    if not index_raw.isdigit() or int(index_raw) >= CHOICE_COUNT:
        raise ValueError(f"correct_index {index_raw!r} is not 0..3")
    correct = int(index_raw)
    return ParsedRow(
        line,
        kind,
        prompt,
        category,
        difficulty,
        tuple((text, idx == correct) for idx, text in enumerate(choices)),
        None,
        None,
        media_file,
        raw,
    )
```

**Note on that one import.** `prompt_digest` lives in `db/repositories/questions.py`, and `tests/test_layering.py` forbids `imports/` from naming `triviador.db`. Move `prompt_digest` to `backend/src/triviador/domain/questions/digest.py`... **no** — the domain is closed to this plan. Move it instead to `backend/src/triviador/media/../` — also wrong. Do this: create `backend/src/triviador/imports/digest.py` holding the function, have `db/repositories/questions.py` import it from there (`from triviador.imports.digest import prompt_digest`) and keep re-exporting it in its `__all__` so Plan 6's `cli.py` import keeps working. One definition, no layering violation, no domain change. Delete the `# noqa` import above and use `from triviador.imports.digest import prompt_digest`.

- [ ] **Step 4: Run the parser test**

Run: `cd backend && uv run pytest tests/imports tests/db/test_seed_questions.py -q`
Expected: PASS — the second path is the regression check on moving `prompt_digest`.

- [ ] **Step 5: Write the failing dry-run route test**

Create `backend/tests/api/test_admin_imports.py`:

```python
import httpx
import pytest

from tests.imports.test_parse import MC, NUM, csv_bytes, zip_bytes
from tests.media.test_pipeline import png
from triviador.api.deps import AppDependencies

pytestmark = pytest.mark.asyncio


async def dry_run(client: httpx.AsyncClient, body: bytes, filename: str) -> httpx.Response:
    return await client.post(
        "/api/admin/questions/import/dry-run",
        content=body,
        headers={"Content-Type": "application/octet-stream", "X-Filename": filename},
    )


async def test_a_player_cannot_dry_run(signed_in: httpx.AsyncClient) -> None:
    assert (await dry_run(signed_in, csv_bytes(NUM), "b.csv")).status_code == 403


async def test_a_clean_upload_reports_zero_rejections_and_stages_the_file(
    admin_client: httpx.AsyncClient, deps: AppDependencies
) -> None:
    response = await dry_run(admin_client, csv_bytes(MC, NUM), "bank.csv")
    assert response.status_code == 201
    body = response.json()
    assert (body["row_count"], body["rejected_count"]) == (2, 0)
    assert body["status"] == "validated"
    assert body["confirmable"] is True
    assert deps.staging_store.objects[body["staged_key"]] == csv_bytes(MC, NUM)


async def test_nothing_is_written_to_the_bank(
    admin_client: httpx.AsyncClient, deps: AppDependencies
) -> None:
    """§9.3's dry-run invariant, as one assertion: no question, no
    category, no media asset, no public object."""
    before = len(deps.questions_admin.records), len(deps.media_store.objects)
    await dry_run(admin_client, zip_bytes(csv_bytes(MC.replace(",,,", ",,,river.png")),
                                          {"river.png": png(32, 32)}), "bank.zip")
    assert (len(deps.questions_admin.records), len(deps.media_store.objects)) == before
    assert deps.categories.records == {}


async def test_a_rejected_row_makes_the_upload_unconfirmable(
    admin_client: httpx.AsyncClient
) -> None:
    """§10.3: CONFIRM is enabled only when `rejected == 0`. The server says
    so on the report rather than leaving the rule to the client."""
    response = await dry_run(admin_client, csv_bytes(MC, "numeric,No answer,history,easy,,,,,,,,"),
                             "b.csv")
    body = response.json()
    assert (body["row_count"], body["rejected_count"]) == (1, 1)
    assert body["confirmable"] is False


async def test_media_is_validated_during_the_dry_run(admin_client: httpx.AsyncClient) -> None:
    """A row whose image cannot be re-encoded must be rejected *now*.
    Otherwise `rejected == 0` would promise a confirm that fails halfway
    through, which is exactly the partial-write §10.3 forbids."""
    body = zip_bytes(csv_bytes(MC.replace(",,,", ",,,broken.png")), {"broken.png": b"not a png"})
    response = await dry_run(admin_client, body, "bank.zip")
    assert response.json()["rejected_count"] == 1
    assert "broken.png" in response.json()["rejections"][0]["reason"]


async def test_the_rejected_rows_come_back_as_csv(admin_client: httpx.AsyncClient) -> None:
    created = (
        await dry_run(admin_client, csv_bytes(MC, "numeric,No answer,history,easy,,,,,,,,"), "b.csv")
    ).json()
    response = await admin_client.get(
        f"/api/admin/questions/import/{created['import_id']}/rejected.csv"
    )
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/csv")
    lines = response.text.strip().splitlines()
    assert lines[0].endswith(",reason")
    assert "No answer" in lines[1]


async def test_a_bad_header_fails_the_whole_request(admin_client: httpx.AsyncClient) -> None:
    response = await dry_run(admin_client, b"a,b\n1,2", "b.csv")
    assert response.status_code == 422
    assert response.json()["code"] == "validation_failed"


async def test_an_upload_over_the_import_cap_is_refused(
    admin_client: httpx.AsyncClient, deps: AppDependencies
) -> None:
    oversized = b"x" * (deps.settings.import_max_bytes + 1)
    assert (await dry_run(admin_client, oversized, "b.csv")).status_code == 413
```

- [ ] **Step 6: Write the port, the repository, the schemas and the route**

Append to `backend/src/triviador/services/admin.py`:

```python
class ImportStatus(StrEnum):
    """§9.3's four states, closed here because this plan implements the
    machine that walks them. Plan 3 left the column unconstrained on
    purpose — the spec named these in prose only — and `imports/retire.py`
    is now the single writer."""

    VALIDATED = "validated"
    CONFIRMED = "confirmed"
    EXPIRED = "expired"
    CLEANED = "cleaned"


@dataclass(frozen=True)
class ImportRecord:
    import_id: str
    uploaded_by: str
    upload_sha256: str
    filename: str
    staged_key: str | None
    row_count: int
    rejected_count: int
    report: dict[str, Any]
    status: ImportStatus
    expires_at: datetime


class ImportPort(Protocol):
    async def create(
        self,
        *,
        import_id: str,
        uploaded_by: str,
        upload_sha256: str,
        filename: str,
        staged_key: str,
        row_count: int,
        rejected_count: int,
        report: dict[str, Any],
        expires_at: datetime,
    ) -> ImportRecord: ...
    async def get(self, import_id: str) -> ImportRecord | None: ...
```

Create `backend/src/triviador/db/repositories/imports.py`:

```python
"""`question_imports`: the only state that survives between the two phases.

The row is written at dry-run time and is the anchor for everything after
it — the confirm's `FOR UPDATE` (Task 8), the expiry machine's sweep
(Task 9), and the audit trail a confirmed import leaves behind. It is
therefore also the reason the row is written *before* the staged object:
an object with no row is invisible to all three.
"""

from datetime import datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from triviador.db.models.content import QuestionImport
from triviador.services.admin import ImportRecord, ImportStatus


def _to_record(row: QuestionImport) -> ImportRecord:
    return ImportRecord(
        import_id=row.id,
        uploaded_by=row.uploaded_by,
        upload_sha256=row.upload_sha256,
        filename=row.filename,
        staged_key=row.staged_key,
        row_count=row.row_count,
        rejected_count=row.rejected_count,
        report=row.report or {},
        status=ImportStatus(row.status),
        expires_at=row.expires_at,
    )


class QuestionImportRepository:
    """Implements `services.admin.ImportPort`."""

    def __init__(self, sessionmaker: async_sessionmaker[AsyncSession]) -> None:
        self._sessionmaker = sessionmaker

    async def create(
        self,
        *,
        import_id: str,
        uploaded_by: str,
        upload_sha256: str,
        filename: str,
        staged_key: str,
        row_count: int,
        rejected_count: int,
        report: dict[str, Any],
        expires_at: datetime,
    ) -> ImportRecord:
        row = QuestionImport(
            id=import_id,
            uploaded_by=uploaded_by,
            upload_sha256=upload_sha256,
            filename=filename,
            staged_key=staged_key,
            row_count=row_count,
            rejected_count=rejected_count,
            report=report,
            status=ImportStatus.VALIDATED.value,
            expires_at=expires_at,
        )
        async with self._sessionmaker() as session, session.begin():
            session.add(row)
        return _to_record(row)

    async def get(self, import_id: str) -> ImportRecord | None:
        async with self._sessionmaker() as session:
            row = await session.get(QuestionImport, import_id)
        return None if row is None else _to_record(row)
```

Task 8 adds `apply_if_confirmable` and its `BankWriter`; Task 9 adds `mark_expired`, `retirable_staged` and `mark_cleaned`. Nothing else ever writes this table.

Create `backend/src/triviador/api/schemas/admin/imports.py`:

```python
from datetime import datetime

from pydantic import BaseModel, ConfigDict

from triviador.services.admin import ImportStatus


class ImportRejection(BaseModel):
    model_config = ConfigDict(extra="forbid")

    line: int
    reason: str


class ImportNotice(BaseModel):
    """§10.2's warning channel: duplicate prompts, inside this upload or
    already in the bank. Distinct from `ImportRejection` in the contract
    as well as in the code, so 7B cannot render one as the other."""

    model_config = ConfigDict(extra="forbid")

    line: int
    reason: str


class ImportSummary(BaseModel):
    """`confirmable` is computed by the server, not inferred by the client
    from `rejected_count == 0`. The rule is §10.3's, it also depends on
    status and expiry, and a client that re-derives it will eventually
    derive it differently."""

    model_config = ConfigDict(extra="forbid")

    import_id: str
    upload_sha256: str
    filename: str
    staged_key: str | None
    row_count: int
    rejected_count: int
    rejections: list[ImportRejection]
    notices: list[ImportNotice]
    status: ImportStatus
    confirmable: bool
    expires_at: datetime
```

Create `backend/src/triviador/api/http/admin/imports.py` — phase one only:

```python
"""§10.3's two phases. This module is phase one; Task 8 appends phase two.

**The row is written before the object is staged.** The two stores share
no transaction, so one of them is first, and the choice is not arbitrary:
staging the object first and then failing to insert the row would leave an
untracked upload — full of correct answers — in a bucket nothing will ever
sweep, because every sweep starts from a `question_imports` row. Row
first, object second means the worst case is a row whose staged object is
missing, which `confirm` refuses with a reason and the expiry machine
retires on schedule.

**The filename arrives in `X-Filename`,** because the body is the file
(the same raw-body decision the media route documents). It decides only
`.zip` versus `.csv` parsing and what the staged object is called; it
never becomes a path.
"""

import csv
import io
import uuid
from datetime import timedelta

from fastapi import APIRouter, Header, Request
from fastapi.responses import PlainTextResponse

from triviador.api.deps import AdminPrincipal, Deps
from triviador.api.errors import ApiError, ApiErrorCode
from triviador.api.http.admin.media import read_capped
from triviador.api.schemas.admin.imports import ImportNotice, ImportRejection, ImportSummary
from triviador.imports.digest import prompt_digest
from triviador.imports.parse import (
    Notice,
    ParsedImport,
    ParsedRow,
    Rejection,
    UploadRejected,
    parse_upload,
)
from triviador.media.pipeline import MediaRejected
from triviador.services.admin import ImportRecord, ImportStatus

router = APIRouter(prefix="/questions/import", tags=["admin"])

import hashlib


def _summary(record: ImportRecord, *, now: datetime) -> ImportSummary:
    """`confirmable` is three facts, not two.

    Status and rejection count are §10.3's rule; `expires_at` is §9.3's,
    and leaving it out would show a green CONFIRM button on an import the
    server will refuse — the client would then be the only place the
    expiry rule was *not* applied.
    """
    rejections = [
        ImportRejection(line=int(item["line"]), reason=str(item["reason"]))
        for item in record.report.get("rejections", ())
    ]
    notices = [
        ImportNotice(line=int(item["line"]), reason=str(item["reason"]))
        for item in record.report.get("notices", ())
    ]
    return ImportSummary(
        import_id=record.import_id,
        upload_sha256=record.upload_sha256,
        filename=record.filename,
        staged_key=record.staged_key,
        row_count=record.row_count,
        rejected_count=record.rejected_count,
        rejections=rejections,
        notices=notices,
        status=record.status,
        confirmable=(
            record.status is ImportStatus.VALIDATED
            and record.rejected_count == 0
            and record.expires_at > now
        ),
        expires_at=record.expires_at,
    )


async def _bank_duplicates(deps: Deps, rows: Sequence[ParsedRow]) -> tuple[Notice, ...]:
    """§10.2's other half: a prompt the bank already has is a warning here
    too, and the dry-run report is the only screen that can show it before
    the rows are applied.

    One query for the whole file, not one per row: a 500-row import would
    otherwise open 500 round trips to answer a question that is a single
    `WHERE prompt_hash IN (...)`.
    """
    digests = {row.line: prompt_digest(row.prompt) for row in rows}
    known = await deps.questions_admin.existing_prompt_digests(frozenset(digests.values()))
    return tuple(
        Notice(line=line, reason="a question with this prompt is already in the bank")
        for line, digest in sorted(digests.items())
        if digest in known
    )


async def _reject_unusable_media(deps: Deps, parsed: ParsedImport) -> tuple[Rejection, ...]:
    """Validate every referenced image now, and throw the result away.

    §9.3 re-encodes at confirm time, so this is the same work twice — and
    it is the price of §10.3's promise that `rejected == 0` means the
    confirm will not fail halfway. An import that discovered a corrupt
    JPEG during phase two would have to roll back a transaction the admin
    was told was safe.
    """
    extra: list[Rejection] = []
    for row in parsed.rows:
        if row.media_file is None:
            continue
        try:
            await deps.normalizer.normalize(parsed.media[row.media_file])
        except MediaRejected as exc:
            extra.append(
                Rejection(line=row.line, reason=f"{row.media_file}: {exc.reason}", raw=row.raw)
            )
    return tuple(extra)


@router.post("/dry-run", status_code=201)
async def dry_run(
    request: Request,
    deps: Deps,
    principal: AdminPrincipal,
    x_filename: str = Header(default="upload.csv"),
) -> ImportSummary:
    raw = await read_capped(request, deps.settings.import_max_bytes)
    try:
        parsed = parse_upload(raw, filename=x_filename)
    except UploadRejected as exc:
        raise ApiError(ApiErrorCode.VALIDATION_FAILED, 422, str(exc)) from exc

    media_rejections = await _reject_unusable_media(deps, parsed)
    rejected = tuple(parsed.rejections) + media_rejections
    accepted = tuple(r for r in parsed.rows if r.line not in {x.line for x in media_rejections})
    notices = tuple(parsed.notices) + await _bank_duplicates(deps, accepted)

    import_id = uuid.uuid4().hex
    staged_key = f"{import_id}/{x_filename.rsplit('/', 1)[-1]}"
    now = deps.clock.now()
    record = await deps.imports.create(
        import_id=import_id,
        uploaded_by=str(principal.user_id),
        upload_sha256=hashlib.sha256(raw).hexdigest(),
        filename=x_filename,
        staged_key=staged_key,
        row_count=len(accepted),
        rejected_count=len(rejected),
        report={
            "columns": list(next(iter(rejected), None).raw) if rejected else [],
            "rejections": [
                {"line": r.line, "reason": r.reason, "raw": dict(r.raw)} for r in rejected
            ],
            "notices": [{"line": n.line, "reason": n.reason} for n in notices],
        },
        expires_at=now + timedelta(hours=deps.settings.import_ttl_hours),
    )
    await deps.staging_store.put(
        staged_key, raw, content_type="application/zip" if x_filename.endswith(".zip")
        else "text/csv"
    )
    return _summary(record, now=now)


@router.get("/{import_id}/rejected.csv", response_class=PlainTextResponse)
async def rejected_csv(import_id: str, deps: Deps, principal: AdminPrincipal) -> PlainTextResponse:
    """The original rows plus a `reason` column — §10.3's fix-and-repeat
    loop. Built from the stored report, not from the staged object: the
    object may already have been retired, and the report is what the
    verdict was actually computed from."""
    record = await deps.imports.get(import_id)
    if record is None:
        raise ApiError(ApiErrorCode.NOT_FOUND, 404, "no such import")
    rejections = record.report.get("rejections", [])
    columns = list(record.report.get("columns") or [])
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=[*columns, "reason"], extrasaction="ignore")
    writer.writeheader()
    for item in rejections:
        writer.writerow({**item.get("raw", {}), "reason": item["reason"]})
    return PlainTextResponse(buffer.getvalue(), media_type="text/csv; charset=utf-8")
```

Add `IMPORT_NOT_CONFIRMABLE = "import_not_confirmable"` to `ApiErrorCode` (Task 8 raises it), wire `imports: ImportPort` and `staging_store: ImportStagingStore` into `AppDependencies`, `build_dependencies` (`QuestionImportRepository(sessions)`, `S3ImportStagingStore(...)` with `bucket=settings.staging_bucket`) and the `deps` fixture (`FakeImports`, `FakeStagingStore`), and include the router.

Update the last assertion of `tests/api/test_admin_media.py::test_every_exempt_upload_path_is_a_real_route` to `assert set(UPLOAD_PATHS) - paths == set()`.

- [ ] **Step 7: Run everything and commit**

Run: `cd backend && uv run pytest -q && uv run mypy && uv run ruff check .`

```bash
git add backend/src/triviador backend/tests
git commit -m "feat(admin): import dry-run — parse, validate, stage, write nothing"
```

---

## Task 8: The import, phase two — apply exactly that upload, once

§9.3's confirm ordering, verbatim, including the two things that make it safe: the sha compared is *recomputed staged object* against *dry-run-stored*, never the client's claim; and the second concurrent confirm loses at `FOR UPDATE` and gets a 409.

**Files:**
- Modify: `backend/src/triviador/db/repositories/imports.py`, `backend/src/triviador/db/repositories/question_admin.py`, `backend/src/triviador/services/admin.py`
- Modify: `backend/src/triviador/api/http/admin/imports.py`
- Test: `backend/tests/api/test_admin_imports.py` (append), `backend/tests/db/test_admin_repositories.py` (append)

**Interfaces:**
- Produces:
  - `services.admin.ImportedImage(asset_id, mime_type, width, height, byte_size, storage_key)` and `services.admin.ImportedQuestion(category_slug, kind, prompt, difficulty, media_file, choices, numeric_answer, unit)` — plain data, so the port names neither a SQLAlchemy session nor a parser type
  - `services.admin.ImportPort.apply_if_confirmable(import_id, *, rows, images, uploaded_by, now) -> bool` — §9.3's single transaction, from `FOR UPDATE` to `COMMIT`
  - `POST /api/admin/questions/import/{import_id}/confirm` → 200 `ImportSummary`

- [ ] **Step 1: Write the failing tests**

Append to `backend/tests/api/test_admin_imports.py`:

```python
async def confirm(client: httpx.AsyncClient, import_id: str) -> httpx.Response:
    return await client.post(f"/api/admin/questions/import/{import_id}/confirm")


async def test_confirm_writes_every_row_once(
    admin_client: httpx.AsyncClient, deps: AppDependencies
) -> None:
    created = (await dry_run(admin_client, csv_bytes(MC, NUM), "bank.csv")).json()
    response = await confirm(admin_client, created["import_id"])
    assert response.status_code == 200
    assert response.json()["status"] == "confirmed"
    assert len(deps.questions_admin.records) == 2


async def test_a_second_confirm_is_409(admin_client: httpx.AsyncClient) -> None:
    """The row is `confirmed` and can never be applied again — which is
    what makes the button safe to double-click."""
    created = (await dry_run(admin_client, csv_bytes(NUM), "b.csv")).json()
    assert (await confirm(admin_client, created["import_id"])).status_code == 200
    second = await confirm(admin_client, created["import_id"])
    assert second.status_code == 409
    assert second.json()["code"] == "import_not_confirmable"


async def test_an_upload_with_rejections_cannot_be_confirmed(
    admin_client: httpx.AsyncClient, deps: AppDependencies
) -> None:
    created = (
        await dry_run(admin_client, csv_bytes(MC, "numeric,No answer,history,easy,,,,,,,,"), "b.csv")
    ).json()
    response = await confirm(admin_client, created["import_id"])
    assert response.status_code == 409
    assert deps.questions_admin.records == {}


async def test_a_staged_object_that_changed_underneath_is_refused(
    admin_client: httpx.AsyncClient, deps: AppDependencies
) -> None:
    """The comparison §9.3 specifies: recomputed-from-staged against
    dry-run-stored. Nothing here trusts a sha the client sent."""
    created = (await dry_run(admin_client, csv_bytes(NUM), "b.csv")).json()
    deps.staging_store.objects[created["staged_key"]] = csv_bytes(MC)
    response = await confirm(admin_client, created["import_id"])
    assert response.status_code == 409
    assert "changed" in response.json()["message"]


async def test_an_expired_import_cannot_be_confirmed(
    admin_client: httpx.AsyncClient, deps: AppDependencies
) -> None:
    """§9.3 gives a staged upload a TTL. Without this check a validated
    import stays confirmable forever, and the TTL only bites if an
    operator happens to run `media-gc` first — which is a rule enforced by
    a cron job that does not exist yet."""
    created = (await dry_run(admin_client, csv_bytes(NUM), "b.csv")).json()
    deps.clock.advance(timedelta(hours=deps.settings.import_ttl_hours + 1))
    response = await confirm(admin_client, created["import_id"])
    assert response.status_code == 409
    assert response.json()["code"] == "import_not_confirmable"
    assert "expired" in response.json()["message"]


def test_confirmable_is_false_once_the_upload_expires() -> None:
    """`_summary` is a pure function and is tested as one — §6.1 defines
    three import routes and no "read one import", so there is nowhere to
    observe this through HTTP without inventing a fourth.

    The client renders CONFIRM from this field; if the server computed it
    from rejections alone, 7B would show a live button on a dead import.
    """
    from datetime import UTC, datetime, timedelta

    from triviador.api.http.admin.imports import _summary
    from triviador.services.admin import ImportRecord, ImportStatus

    now = datetime(2026, 8, 20, 12, 0, tzinfo=UTC)
    record = ImportRecord(
        import_id="imp-1",
        uploaded_by="admin",
        upload_sha256="sha",
        filename="b.csv",
        staged_key="imp-1/b.csv",
        row_count=1,
        rejected_count=0,
        report={"rejections": [], "notices": []},
        status=ImportStatus.VALIDATED,
        expires_at=now - timedelta(seconds=1),
    )
    assert _summary(record, now=now).confirmable is False
    assert _summary(record, now=now - timedelta(hours=2)).confirmable is True


async def test_a_duplicate_prompt_is_a_notice_and_the_upload_stays_confirmable(
    admin_client: httpx.AsyncClient
) -> None:
    """§10.2's rule, in the place it is easiest to get wrong: a repeated
    prompt inside one file, and a prompt the bank already holds, are both
    warnings. Rejecting either would make the upload unconfirmable, which
    is a block by another name."""
    first = await dry_run(admin_client, csv_bytes(NUM), "b.csv")
    await confirm(admin_client, first.json()["import_id"])

    again = (await dry_run(admin_client, csv_bytes(NUM, NUM), "b.csv")).json()
    assert again["rejected_count"] == 0
    assert again["confirmable"] is True
    reasons = " ".join(n["reason"] for n in again["notices"])
    assert "already in the bank" in reasons
    assert "same prompt as line" in reasons


async def test_a_missing_staged_object_is_refused_with_a_reason(
    admin_client: httpx.AsyncClient, deps: AppDependencies
) -> None:
    created = (await dry_run(admin_client, csv_bytes(NUM), "b.csv")).json()
    del deps.staging_store.objects[created["staged_key"]]
    response = await confirm(admin_client, created["import_id"])
    assert response.status_code == 409
    assert response.json()["code"] == "import_not_confirmable"


async def test_confirm_writes_the_media_blobs_before_the_rows(
    admin_client: httpx.AsyncClient, deps: AppDependencies
) -> None:
    body = zip_bytes(csv_bytes(MC.replace(",,,", ",,,river.png")), {"river.png": png(40, 20)})
    created = (await dry_run(admin_client, body, "bank.zip")).json()
    await confirm(admin_client, created["import_id"])
    question = next(iter(deps.questions_admin.records.values()))
    assert question.media_asset_id is not None
    key = f"{question.media_asset_id[:2]}/{question.media_asset_id}.webp"
    assert deps.media_store.objects[key][:4] == b"RIFF"


async def test_an_unknown_category_in_the_file_is_created_by_confirm(
    admin_client: httpx.AsyncClient, deps: AppDependencies
) -> None:
    """The slug in the file is authoritative at confirm time: the dry-run
    already told the admin how many rows carry it, and refusing here would
    make every first import of a new topic a two-step dance."""
    created = (await dry_run(admin_client, csv_bytes(NUM), "b.csv")).json()
    await confirm(admin_client, created["import_id"])
    assert {c.slug for c in deps.categories.records.values()} == {"history"}
```

Append the concurrency test to `backend/tests/db/test_admin_repositories.py`:

```python
async def test_two_concurrent_confirms_cannot_both_apply(sessions, clean_db) -> None:
    """§9.3: "the second loses at `FOR UPDATE` and returns 409". Asserted
    against real PostgreSQL, because the property is the lock's, not the
    code's."""
    import asyncio

    repository = QuestionImportRepository(sessions)
    record = await repository.create(
        import_id="imp-1",
        uploaded_by="admin-1",
        upload_sha256="sha",
        filename="b.csv",
        staged_key="imp-1/b.csv",
        row_count=1,
        rejected_count=0,
        report={"rejections": []},
        expires_at=datetime.now(UTC) + timedelta(hours=1),
    )
    assert record.status is ImportStatus.VALIDATED

    async def apply() -> bool:
        return await repository.apply_if_confirmable(
            "imp-1",
            rows=(),
            images={},
            uploaded_by="admin-1",
            now=datetime.now(UTC),
        )

    first, second = await asyncio.gather(apply(), apply())
    assert sorted([first, second]) == [False, True]


async def test_confirm_writes_every_kind_of_row_in_one_transaction(sessions, clean_db) -> None:
    """The composition this task actually adds, against the real schema.

    Every individual statement here is proven elsewhere — questions and
    their children in `test_question_admin.py`, the media upsert in
    `test_media_repository.py`, category-ensure-in-the-caller's-transaction
    in `test_seed_questions.py`. What has never run against PostgreSQL is
    all five landing *together* inside the locked transaction with the
    status flip. A column-name typo, an FK ordering mistake or a
    `Decimal`/`NUMERIC` mismatch in that composition would pass every fake
    and fail on the first real import.
    """
    from sqlalchemy import text as sql

    await _seed_user(sessions, "admin-1")
    repository = QuestionImportRepository(sessions)
    await repository.create(
        import_id="imp-write",
        uploaded_by="admin-1",
        upload_sha256="sha",
        filename="bank.zip",
        staged_key="imp-write/bank.zip",
        row_count=2,
        rejected_count=0,
        report={"rejections": [], "notices": []},
        expires_at=datetime.now(UTC) + timedelta(hours=1),
    )

    applied = await repository.apply_if_confirmable(
        "imp-write",
        rows=(
            ImportedQuestion(
                category_slug="geography",
                kind="multiple_choice",
                prompt="Which river runs through Prague?",
                difficulty="easy",
                media_file="river.png",
                choices=(("Vltava", True), ("Elbe", False), ("Morava", False), ("Ohře", False)),
                numeric_answer=None,
                unit=None,
            ),
            ImportedQuestion(
                category_slug="history",
                kind="numeric",
                prompt="In which year did the Velvet Revolution begin?",
                difficulty="easy",
                media_file=None,
                choices=None,
                numeric_answer=Decimal("1989"),
                unit=None,
            ),
        ),
        images={
            "river.png": ImportedImage(
                asset_id="c" * 64,
                mime_type="image/webp",
                width=800,
                height=400,
                byte_size=1234,
                storage_key="cc/ccc.webp",
            )
        },
        uploaded_by="admin-1",
        now=datetime.now(UTC),
    )
    assert applied is True

    async with sessions() as session:
        counts = {
            table: (await session.execute(sql(f"SELECT count(*) FROM {table}"))).scalar_one()
            for table in (
                "categories",
                "questions",
                "question_choices",
                "question_numeric",
                "media_assets",
            )
        }
        status = (
            await session.execute(
                sql("SELECT status FROM question_imports WHERE id = 'imp-write'")
            )
        ).scalar_one()
        answer = (
            await session.execute(sql("SELECT correct_value FROM question_numeric"))
        ).scalar_one()
        attached = (
            await session.execute(
                sql("SELECT media_asset_id FROM questions WHERE kind = 'multiple_choice'")
            )
        ).scalar_one()

    # Two categories created from slugs that did not exist, two questions,
    # four choices for the MC one, one numeric answer, one media asset.
    assert counts == {
        "categories": 2,
        "questions": 2,
        "question_choices": 4,
        "question_numeric": 1,
        "media_assets": 1,
    }
    assert status == "confirmed"
    assert answer == Decimal("1989")
    assert attached == "c" * 64


async def test_an_expired_import_cannot_be_applied_even_with_zero_rejections(
    sessions, clean_db
) -> None:
    """The check that belongs under the lock, not only in the route: an
    import whose TTL passed while the confirm was in flight must lose."""
    repository = QuestionImportRepository(sessions)
    await repository.create(
        import_id="imp-2",
        uploaded_by="admin-1",
        upload_sha256="sha",
        filename="b.csv",
        staged_key="imp-2/b.csv",
        row_count=1,
        rejected_count=0,
        report={"rejections": [], "notices": []},
        expires_at=datetime.now(UTC) - timedelta(seconds=1),
    )
    assert not await repository.apply_if_confirmable(
        "imp-2", rows=(), images={}, uploaded_by="admin-1", now=datetime.now(UTC)
    )
```

- [ ] **Step 2: Run them and watch them fail**

Run: `cd backend && uv run pytest tests/api/test_admin_imports.py tests/db/test_admin_repositories.py -q`
Expected: FAIL — 404 on `/confirm`, and `AttributeError: apply_if_confirmable`.

- [ ] **Step 3: Write the transactional half**

Extend `services/admin.py` with the two data shapes and one method:

```python
@dataclass(frozen=True)
class ImportedImage:
    """A blob the confirm has already written, described for the row that
    will reference it. No bytes: they are in the bucket by the time this
    exists."""

    asset_id: str
    mime_type: str
    width: int
    height: int
    byte_size: int
    storage_key: str


@dataclass(frozen=True)
class ImportedQuestion:
    """One row of a validated import, in the vocabulary of the bank.

    `category_slug` rather than `category_id`: the category may not exist
    until the confirming transaction creates it, so resolution has to
    happen inside that transaction and cannot be done by the caller.
    """

    category_slug: str
    kind: str
    prompt: str
    difficulty: str
    media_file: str | None
    choices: tuple[tuple[str, bool], ...] | None
    numeric_answer: Decimal | None
    unit: str | None
```

```python
    async def apply_if_confirmable(
        self,
        import_id: str,
        *,
        rows: Sequence[ImportedQuestion],
        images: Mapping[str, ImportedImage],
        uploaded_by: str,
        now: datetime,
    ) -> bool:
        """§9.3's transaction, from `FOR UPDATE` to `COMMIT`.

        Everything the import inserts — categories, questions, choices,
        numeric answers, media asset rows — happens inside this call,
        because it all has to be inside the transaction that holds the
        lock. Passing plain data rather than a callback keeps the
        SQLAlchemy session on the `db/` side of the port: a Protocol whose
        parameter is a session either names `AsyncSession` in `services/`
        (which the layering gate forbids) or widens it to `object`, which
        no implementation can narrow back without breaking
        contravariance — `mypy --strict` rejects both.

        `False` means the row was not confirmable under the lock: already
        confirmed, expired, or carrying rejections. The caller turns that
        into a 409; it is never an exception, because losing this race is
        an ordinary outcome of two admins clicking at once.
        """
        ...
```

In `backend/src/triviador/db/repositories/imports.py`:

```python
    async def apply_if_confirmable(
        self,
        import_id: str,
        *,
        rows: Sequence[ImportedQuestion],
        images: Mapping[str, ImportedImage],
        uploaded_by: str,
        now: datetime,
    ) -> bool:
        async with self._sessionmaker() as session, session.begin():
            row = await session.get(QuestionImport, import_id, with_for_update=True)
            if row is None:
                return False
            # All three conditions re-checked *under the lock*, not before
            # it: the values the route read a moment ago are exactly the
            # ones a concurrent confirm — or the clock — is about to
            # change. `expires_at` belongs here as much as `status` does;
            # without it a validated import stays confirmable forever and
            # §9.3's TTL only takes effect if `media-gc` happens to run.
            if row.status != ImportStatus.VALIDATED.value:
                return False
            if row.rejected_count != 0:
                return False
            if row.expires_at <= now:
                return False
            await self._write_bank(session, rows, images, uploaded_by)
            row.status = ImportStatus.CONFIRMED.value
            row.confirmed_at = now
            return True
```

...and the private writer in the same class — everything one confirm inserts,
against the session that holds the lock:

```python
    async def _write_bank(
        self,
        session: AsyncSession,
        rows: Sequence[ImportedQuestion],
        images: Mapping[str, ImportedImage],
        uploaded_by: str,
    ) -> None:
        """Every insert uses the session it is handed, never
        `self._sessionmaker`: opening a second session would put these
        writes in a second transaction, and §10.3's "no partial writes"
        would then mean "no partial writes unless the process dies between
        two of them".

        Categories are created on the fly. The dry-run already reported
        how many rows carry each slug, so refusing an unknown one here
        would turn the first import of a new topic into a two-step dance
        for no safety.
        """
        categories = await self._ensure_categories(session, rows)
        await self._ensure_assets(session, images, uploaded_by)
        for row in rows:
            write = QuestionWrite(
                kind=row.kind,
                prompt=row.prompt,
                category_id=categories[row.category_slug],
                difficulty=row.difficulty,
                media_asset_id=(
                    images[row.media_file].asset_id if row.media_file else None
                ),
                choices=row.choices,
                numeric_answer=row.numeric_answer,
                unit=row.unit,
            )
            # The same shape rule the hand-editor obeys, from the same
            # function — an importer with its own copy is an importer that
            # will one day accept three choices.
            _validate(write)
            question_id = str(uuid4())
            session.add(
                Question(
                    id=question_id,
                    version=1,
                    kind=write.kind,
                    prompt=write.prompt,
                    category_id=write.category_id,
                    difficulty=write.difficulty,
                    media_asset_id=write.media_asset_id,
                    is_active=True,
                    prompt_hash=prompt_digest(write.prompt),
                )
            )
            await session.flush()
            QuestionAdminRepository._write_children(session, question_id, write)

    @staticmethod
    async def _ensure_categories(
        session: AsyncSession, rows: Sequence[ImportedQuestion]
    ) -> dict[str, str]:
        slugs = {row.category_slug for row in rows}
        existing = {
            row.slug: row.id
            for row in (
                await session.execute(select(Category).where(Category.slug.in_(slugs)))
            ).scalars()
        }
        for slug in sorted(slugs - set(existing)):
            category = Category(id=str(uuid4()), slug=slug, name=slug.replace("-", " ").title())
            session.add(category)
            existing[slug] = category.id
        await session.flush()
        return existing

    @staticmethod
    async def _ensure_assets(
        session: AsyncSession, images: Mapping[str, ImportedImage], uploaded_by: str
    ) -> None:
        for image in images.values():
            # `ON CONFLICT DO NOTHING`, because the same picture may
            # already be in the bank from an earlier import — content
            # addressing makes that the *same* asset, not a collision.
            await session.execute(
                insert(MediaAsset)
                .values(
                    id=image.asset_id,
                    mime_type=image.mime_type,
                    width=image.width,
                    height=image.height,
                    byte_size=image.byte_size,
                    storage_key=image.storage_key,
                    created_by=uploaded_by,
                )
                .on_conflict_do_nothing(index_elements=[MediaAsset.id])
            )
        await session.flush()
```

...with `from triviador.db.repositories.question_admin import QuestionAdminRepository, _validate`
and `from triviador.imports.digest import prompt_digest` imported here. Nothing new lands in
`AppDependencies`: the route already holds `deps.imports`, and the data it passes is the plain
`ImportedQuestion`/`ImportedImage` it builds itself.

- [ ] **Step 4: Write the route**

Append to `backend/src/triviador/api/http/admin/imports.py`:

```python
@router.post("/{import_id}/confirm")
async def confirm_import(
    import_id: str, deps: Deps, principal: AdminPrincipal
) -> ImportSummary:
    """§9.3's order, and the reason each step is where it is.

        read staged object          — the upload, not what the client sent now
        recompute sha256            — and compare against the dry-run's
        validate + re-encode media  — CPU-bound, before any lock is taken
        write public blobs          — idempotent by content addressing
        BEGIN … FOR UPDATE … COMMIT — the only step that can lose a race

    Concurrent confirms duplicate the preprocessing and write the same
    blobs twice, which is safe precisely because the blobs are addressed
    by their content; only the transaction is serialised.
    """
    now = deps.clock.now()
    record = await deps.imports.get(import_id)
    if record is None:
        raise ApiError(ApiErrorCode.NOT_FOUND, 404, "no such import")
    if record.status is not ImportStatus.VALIDATED or record.rejected_count != 0:
        raise ApiError(
            ApiErrorCode.IMPORT_NOT_CONFIRMABLE,
            409,
            f"this import is {record.status.value} with {record.rejected_count} rejected rows",
        )
    if record.expires_at <= now:
        # Refused here for the message, and again under the lock for the
        # rule (`apply_if_confirmable`). §9.3 sets a TTL on the staged
        # upload; an import that outlived it must not be applicable just
        # because `media-gc` has not run since.
        raise ApiError(
            ApiErrorCode.IMPORT_NOT_CONFIRMABLE,
            409,
            "this import expired; upload it again",
        )
    if record.staged_key is None:
        raise ApiError(
            ApiErrorCode.IMPORT_NOT_CONFIRMABLE, 409, "the staged upload has been retired"
        )

    staged = await deps.staging_store.open(record.staged_key)
    if staged is None:
        raise ApiError(
            ApiErrorCode.IMPORT_NOT_CONFIRMABLE, 409, "the staged upload is no longer available"
        )
    if hashlib.sha256(staged).hexdigest() != record.upload_sha256:
        raise ApiError(
            ApiErrorCode.IMPORT_NOT_CONFIRMABLE,
            409,
            "the staged upload changed since it was validated; run the dry-run again",
        )

    parsed = parse_upload(staged, filename=record.filename)
    normalized = {}
    try:
        for row in parsed.rows:
            if row.media_file is not None and row.media_file not in normalized:
                normalized[row.media_file] = await deps.normalizer.normalize(
                    parsed.media[row.media_file]
                )
    except MediaRejected as exc:
        # Dry-run validated these exact bytes — the sha match above proves
        # they *are* the same bytes — so this is unreachable within one
        # running process. It is not unreachable across a deploy: the
        # limits live on `ImageNormalizer`, built from settings at process
        # start, and an operator who tightens `media_max_bytes` between an
        # admin's dry-run and their confirm (well inside `IMPORT_TTL_HOURS`)
        # makes an image that passed then fail now. That is an ordinary
        # "run the dry-run again", not a server fault, and letting it reach
        # the catch-all handler would report it as a 500.
        raise ApiError(
            ApiErrorCode.IMPORT_NOT_CONFIRMABLE,
            409,
            f"{exc.reason}; the media limits changed since this upload was validated — "
            "run the dry-run again",
        ) from exc
    for image in normalized.values():
        await deps.media_store.put(
            image.storage_key,
            image.data,
            content_type=image.mime_type,
            cache_control=CACHE_CONTROL,
        )

    applied = await deps.imports.apply_if_confirmable(
        import_id,
        rows=tuple(
            ImportedQuestion(
                category_slug=row.category_slug,
                kind=row.kind,
                prompt=row.prompt,
                difficulty=row.difficulty,
                media_file=row.media_file,
                choices=row.choices,
                numeric_answer=row.numeric_answer,
                unit=row.unit,
            )
            for row in parsed.rows
        ),
        images={
            name: ImportedImage(
                asset_id=image.sha256,
                mime_type=image.mime_type,
                width=image.width,
                height=image.height,
                byte_size=image.byte_size,
                storage_key=image.storage_key,
            )
            for name, image in normalized.items()
        },
        uploaded_by=str(principal.user_id),
        now=now,
    )
    if not applied:
        # Lost the `FOR UPDATE` race, or the row changed underneath. The
        # blobs written above stay; they are content-addressed, and
        # `media-gc` collects them if nothing ends up referencing them.
        raise ApiError(
            ApiErrorCode.IMPORT_NOT_CONFIRMABLE, 409, "this import was already confirmed"
        )
    # Same repair as the upload route, for the same window: the blobs were
    # written before the transaction (§9.3's order), so a sweep in between
    # could have taken one. The bytes are still in memory here.
    for image in normalized.values():
        await repair_blob(deps, image)

    confirmed = await deps.imports.get(import_id)
    assert confirmed is not None
    return _summary(confirmed, now=now)
```

...with `from triviador.api.http.admin.media import CACHE_CONTROL, read_capped, repair_blob` and
`from triviador.services.admin import ImportedImage, ImportedQuestion, ImportStatus` at the top
of the module. The route names no `db/` symbol: `ImportedQuestion` and `ImportedImage` are
`services/` dataclasses, which is exactly what lets the port carry them.

- [ ] **Step 5: Run everything and commit**

Run: `cd backend && uv run pytest -q && uv run mypy && uv run ruff check .`

```bash
git add backend/src/triviador backend/tests
git commit -m "feat(admin): import confirm — one transaction, one application, 409 for the loser"
```

---

## Task 9: `media-gc` — the expiry machine and the two-way reference check

§9.3's retirement is a state machine because PostgreSQL and Garage share no transaction; §10.4's collection is two-way because a finished game's event log names the assets it drew. Both are rare and destructive, and both run from one command.

**Files:**
- Create: `backend/src/triviador/imports/retire.py`, `backend/src/triviador/media/gc.py`
- Modify: `backend/src/triviador/db/repositories/imports.py`, `backend/src/triviador/db/repositories/media.py`, `backend/src/triviador/services/admin.py`
- Modify: `backend/src/triviador/cli.py`
- Test: `backend/tests/db/test_media_gc.py`, `backend/tests/imports/test_retire.py`, `backend/tests/db/test_admin_repositories.py` (append)

**Interfaces:**
- Produces:
  - `imports.retire.ImportRetirer(imports, staging, clock).run(*, after_restore: bool, dry_run: bool) -> RetireReport`
  - `media.gc.MediaCollector(assets, store, grace).run(*, now, dry_run: bool) -> GcReport`
  - `services.admin.ImportPort.mark_expired(now, *, all_unconfirmed)`, `.count_expirable(now, *, all_unconfirmed)`, `.retirable_staged() -> tuple[tuple[str, str], ...]`, `.mark_cleaned(import_id)`
  - `services.admin.MediaAssetPort.unreferenced()`, `.claim_unreferenced()`, `.all_storage_keys() -> frozenset[str]`, `.delete(asset_id)`
  - `Settings.media_gc_grace_minutes`
  - `uv run triviador media-gc [--dry-run] [--after-restore]`

- [ ] **Step 1: Write the failing reference-check test**

Create `backend/tests/db/test_media_gc.py`:

```python
"""§10.4: an asset is collectable only when *neither* a question *nor* any
persisted question snapshot names it.

The second half is the one that is easy to get wrong and expensive to get
wrong: `QuestionPoolDrawn` embeds whole `QuestionSnapshot`s in
`game_events.payload`, two levels deep (`pool.multiple_choice[].
media_asset_id` and `...choices[].media_asset_id`). Deleting a blob a
finished game still names does not break the game today — it breaks the
picture on a replay, forever.
"""

import pytest

from triviador.db.repositories.media import MediaAssetRepository

pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="session")]


async def test_an_asset_referenced_by_a_question_is_not_collectable(sessions, clean_db) -> None:
    await _seed_user(sessions, "admin-1")
    await _seed_category(sessions)
    repository = MediaAssetRepository(sessions)
    await _seed_asset(sessions, "a" * 64)
    await _seed_mc_question(sessions, "q-1", media_asset_id="a" * 64)
    assert [r.asset_id for r in await repository.unreferenced()] == []


async def test_an_asset_referenced_only_by_a_choice_is_not_collectable(
    sessions, clean_db
) -> None:
    await _seed_user(sessions, "admin-1")
    await _seed_category(sessions)
    await _seed_asset(sessions, "b" * 64)
    await _seed_mc_question(
        sessions, "q-2", choices=(("A", True, "b" * 64), ("B", False, None))
    )
    assert [r.asset_id for r in await MediaAssetRepository(sessions).unreferenced()] == []


async def test_an_asset_named_only_inside_a_stored_event_is_not_collectable(
    sessions, clean_db
) -> None:
    """The whole point of the two-way check. The question row is gone from
    the bank's active set — this asset is referenced by nothing anybody
    could edit — and it still must not be deleted."""
    await _seed_user(sessions, "admin-1")
    await _seed_asset(sessions, "c" * 64)
    await _seed_event_with_pool(sessions, media_asset_id="c" * 64)
    assert [r.asset_id for r in await MediaAssetRepository(sessions).unreferenced()] == []


async def test_an_asset_nothing_names_is_collectable(sessions, clean_db) -> None:
    await _seed_user(sessions, "admin-1")
    await _seed_asset(sessions, "d" * 64)
    assert [r.asset_id for r in await MediaAssetRepository(sessions).unreferenced()] == ["d" * 64]


async def test_claiming_deletes_the_rows_and_returns_them(sessions, clean_db) -> None:
    """Rows first: the caller deletes the objects afterwards, so a crash
    between the two leaves an orphan object (collectable) rather than a
    row pointing at a missing blob (not)."""
    await _seed_user(sessions, "admin-1")
    await _seed_asset(sessions, "e" * 64)
    repository = MediaAssetRepository(sessions)
    claimed = await repository.claim_unreferenced()
    assert [r.asset_id for r in claimed] == ["e" * 64]
    assert await repository.get("e" * 64) is None
    assert await repository.claim_unreferenced() == ()


async def test_a_question_attached_during_the_sweep_cannot_lose_its_asset(
    sessions, clean_db
) -> None:
    """The race Decision 9 names, against real PostgreSQL.

    The sweep holds `FOR UPDATE` on the `media_assets` row; inserting a
    `questions` row that references it takes `FOR KEY SHARE` on the same
    row, which conflicts. So the attach must *wait*, and once the sweep
    commits its delete, the attach fails on the foreign key — loudly —
    instead of succeeding and pointing at a blob that is gone.
    """
    import asyncio

    from sqlalchemy.exc import IntegrityError

    await _seed_user(sessions, "admin-1")
    await _seed_category(sessions)
    await _seed_asset(sessions, "f" * 64)

    async def attach() -> None:
        await _seed_mc_question(sessions, "q-late", media_asset_id="f" * 64)

    claimed, attached = await asyncio.gather(
        MediaAssetRepository(sessions).claim_unreferenced(),
        attach(),
        return_exceptions=True,
    )
    # Exactly one of the two wins; whichever it is, no question ends up
    # referencing a deleted asset.
    if isinstance(attached, IntegrityError):
        assert [r.asset_id for r in claimed] == ["f" * 64]
    else:
        assert claimed == ()
```

Add `_seed_asset` and `_seed_event_with_pool` to `backend/tests/db/conftest.py`. The second inserts a `games` row and one `game_events` row whose `payload` is a real `QuestionPoolDrawn` shape — copy the nesting from `tests/codec/golden/expansion_to_battle.json` so the test pins the layout that actually exists rather than one invented here.

- [ ] **Step 2: Run it and watch it fail**

Run: `cd backend && uv run pytest tests/db/test_media_gc.py -q`
Expected: FAIL — `AttributeError: 'MediaAssetRepository' object has no attribute 'unreferenced'`.

- [ ] **Step 3: Write the query**

Append to `backend/src/triviador/db/repositories/media.py`:

```python
    async def unreferenced(self) -> tuple[MediaAssetRecord, ...]:
        """The read-only half: what *would* be collected. `--dry-run` and
        the tests use this; the sweep proper uses `claim_unreferenced`,
        which runs the same query with `FOR UPDATE` and deletes."""
        async with self._sessionmaker() as session:
            return await self._unreferenced(session, lock=False)

    @staticmethod
    async def _unreferenced(session: AsyncSession, *, lock: bool) -> tuple[MediaAssetRecord, ...]:
        """§10.4's two-way check, as one statement.

        The event half is a jsonpath scan: `$.**.media_asset_id` finds the
        field at any depth, which is what the snapshot nesting requires
        (question level and choice level, inside an array, inside `pool`).
        It is an unindexed sequential scan over `game_events`, and that is
        the right trade — `media-gc` is a rare command an operator runs,
        and an index maintained on every event append to serve it would be
        a cost paid by every game for a query nobody runs during play.

        `#>> '{}'` unwraps the jsonb scalar to text; a JSON `null`
        unwraps to SQL `NULL`, which the anti-join then ignores — exactly
        right, since a question with no media names no asset.
        """
        statement = text(
            """
            WITH referenced AS (
                SELECT DISTINCT jsonb_path_query(payload, '$.**.media_asset_id') #>> '{}' AS id
                FROM game_events
            )
            SELECT ma.id, ma.mime_type, ma.width, ma.height, ma.byte_size, ma.storage_key
            FROM media_assets ma
            WHERE NOT EXISTS (SELECT 1 FROM questions q WHERE q.media_asset_id = ma.id)
              AND NOT EXISTS (
                    SELECT 1 FROM question_choices c WHERE c.media_asset_id = ma.id
              )
              AND NOT EXISTS (SELECT 1 FROM referenced r WHERE r.id = ma.id)
            ORDER BY ma.id
            """
            # `FOR UPDATE OF ma` locks only the `media_assets` rows this
            # returns — not `questions`, not `game_events`, both of which
            # this statement only reads.
            + (" FOR UPDATE OF ma" if lock else "")
        )
        rows = (await session.execute(statement)).all()
        return tuple(
            MediaAssetRecord(
                asset_id=row[0],
                mime_type=row[1],
                width=row[2],
                height=row[3],
                byte_size=row[4],
                storage_key=row[5],
            )
            for row in rows
        )

    async def claim_unreferenced(self) -> tuple[MediaAssetRecord, ...]:
        """Delete the rows, in one transaction, and hand them back so the
        caller can delete the objects.

        **Rows before objects, and the check repeated under the lock.**
        `SELECT ... FOR UPDATE` on each candidate row is what makes this
        safe against an admin attaching that asset to a question at the
        same moment: PostgreSQL takes `FOR KEY SHARE` on a parent row when
        a child row referencing it is inserted, and that conflicts with
        `FOR UPDATE`. So a question insert either happens before this
        transaction (and the recheck sees it, and the asset is spared) or
        waits until after it (and fails on the foreign key, loudly, rather
        than silently referencing a deleted blob).

        Deleting the row first also decides what a crash leaves behind: an
        object with no row, which the orphan pass collects on the next
        run. The opposite order leaves a row whose object is gone — a
        question that renders a broken image forever.
        """
        async with self._sessionmaker() as session, session.begin():
            candidates = await self._unreferenced(session, lock=True)
            for record in candidates:
                await session.execute(delete(MediaAsset).where(MediaAsset.id == record.asset_id))
            return candidates

    async def all_storage_keys(self) -> frozenset[str]:
        """Every key the database believes in, for the orphan sweep: §10.3
        says a failed import transaction leaves an unreferenced blob and
        `media-gc` removes it safely, and a blob with no row is invisible
        to `unreferenced()`."""
        async with self._sessionmaker() as session:
            keys = (await session.execute(select(MediaAsset.storage_key))).scalars().all()
        return frozenset(keys)

    async def delete(self, asset_id: str) -> None:
        async with self._sessionmaker() as session, session.begin():
            await session.execute(delete(MediaAsset).where(MediaAsset.id == asset_id))
```

...with `from sqlalchemy import delete, select, text` at the top.

Run: `cd backend && uv run pytest tests/db/test_media_gc.py -q` → PASS.

- [ ] **Step 4: Write the failing retirement test**

Create `backend/tests/imports/test_retire.py` — pure, over in-memory fakes:

```python
"""§9.3's expiry, which is a state machine precisely because it cannot be
a transaction.

    validated --(expired by time, or by a restore)--> expired
    expired   --(staged object deleted)-----------> cleaned, staged_key = NULL
    confirmed --(staged object deleted)-----------> confirmed, staged_key = NULL

Every step is retryable, and a crash anywhere leaves a state the next run
resumes from. The tests below kill the process between each pair of steps.
"""

from datetime import UTC, datetime, timedelta

import pytest

from tests.api.fakes import FakeClock, FakeImports, FakeStagingStore
from triviador.imports.retire import ImportRetirer
from triviador.services.admin import ImportStatus

pytestmark = pytest.mark.asyncio

NOW = datetime(2026, 8, 20, 12, 0, tzinfo=UTC)


async def test_an_expired_validated_import_loses_its_staged_object() -> None:
    imports, staging = FakeImports(), FakeStagingStore()
    imports.add("imp-1", status=ImportStatus.VALIDATED, staged_key="k", expires_at=NOW - timedelta(hours=1))
    staging.objects["k"] = b"raw"
    await ImportRetirer(imports=imports, staging=staging, clock=FakeClock(NOW)).run()
    assert imports.records["imp-1"].status is ImportStatus.CLEANED
    assert imports.records["imp-1"].staged_key is None
    assert staging.objects == {}


async def test_an_unexpired_import_is_left_alone() -> None:
    imports, staging = FakeImports(), FakeStagingStore()
    imports.add("imp-1", status=ImportStatus.VALIDATED, staged_key="k", expires_at=NOW + timedelta(hours=1))
    staging.objects["k"] = b"raw"
    await ImportRetirer(imports=imports, staging=staging, clock=FakeClock(NOW)).run()
    assert imports.records["imp-1"].status is ImportStatus.VALIDATED
    assert staging.objects == {"k": b"raw"}


async def test_a_confirmed_import_keeps_its_row_as_an_audit_trail() -> None:
    imports, staging = FakeImports(), FakeStagingStore()
    imports.add("imp-1", status=ImportStatus.CONFIRMED, staged_key="k", expires_at=NOW)
    staging.objects["k"] = b"raw"
    await ImportRetirer(imports=imports, staging=staging, clock=FakeClock(NOW)).run()
    assert imports.records["imp-1"].status is ImportStatus.CONFIRMED
    assert imports.records["imp-1"].staged_key is None
    assert staging.objects == {}


async def test_a_crash_after_the_status_update_is_resumed_by_the_next_run() -> None:
    """The row says `expired` and the object is still there — the state a
    crash between step 1 and step 2 leaves."""
    imports, staging = FakeImports(), FakeStagingStore()
    imports.add("imp-1", status=ImportStatus.EXPIRED, staged_key="k", expires_at=NOW - timedelta(days=2))
    staging.objects["k"] = b"raw"
    await ImportRetirer(imports=imports, staging=staging, clock=FakeClock(NOW)).run()
    assert imports.records["imp-1"].status is ImportStatus.CLEANED
    assert staging.objects == {}


async def test_a_missing_object_still_reaches_cleaned() -> None:
    """A crash between the delete and the second update. Deleting an
    already-absent object is a no-op, so the run finishes the job."""
    imports, staging = FakeImports(), FakeStagingStore()
    imports.add("imp-1", status=ImportStatus.EXPIRED, staged_key="k", expires_at=NOW)
    await ImportRetirer(imports=imports, staging=staging, clock=FakeClock(NOW)).run()
    assert imports.records["imp-1"].status is ImportStatus.CLEANED


async def test_a_dry_run_expires_nothing_and_deletes_nothing() -> None:
    """`media-gc --dry-run` prints "nothing was deleted". Retirement is the
    destructive half — it removes the only copy of an upload an admin may
    still want to confirm — so it has to hear about the flag too."""
    imports, staging = FakeImports(), FakeStagingStore()
    imports.add("imp-1", status=ImportStatus.VALIDATED, staged_key="k", expires_at=NOW - timedelta(hours=1))
    staging.objects["k"] = b"raw"
    report = await ImportRetirer(imports=imports, staging=staging, clock=FakeClock(NOW)).run(
        dry_run=True
    )
    assert report.deleted is False
    assert report.expired == 1          # what it *would* have expired
    assert imports.records["imp-1"].status is ImportStatus.VALIDATED
    assert staging.objects == {"k": b"raw"}


async def test_after_a_restore_every_unconfirmed_import_is_expired() -> None:
    """§9.3: staging is deliberately not backed up (§10.9), so a `validated`
    row that survived the restore offers a confirm that cannot work."""
    imports, staging = FakeImports(), FakeStagingStore()
    imports.add("imp-1", status=ImportStatus.VALIDATED, staged_key="k", expires_at=NOW + timedelta(days=7))
    await ImportRetirer(imports=imports, staging=staging, clock=FakeClock(NOW)).run(
        after_restore=True
    )
    assert imports.records["imp-1"].status is ImportStatus.CLEANED
```

- [ ] **Step 5: Write the retirer and the collector**

Create `backend/src/triviador/imports/retire.py`:

```python
"""The expiry half of §9.3.

**Why the order is fixed.** PostgreSQL and Garage share no transaction, so
"delete the row and the object together" is not available. Deleting the
row first strands an untracked raw upload — full of correct answers — in
the staging bucket, with nothing left to find it by. Deleting the object
first leaves a row that still looks confirmable but whose upload is gone.
So the row is *first marked unconfirmable*, then the object goes, then the
row records that it went.
"""

from dataclasses import dataclass

from triviador.services.admin import ImportPort, ImportStatus
from triviador.services.ports import Clock
from triviador.services.storage import ImportStagingStore


@dataclass(frozen=True)
class RetireReport:
    expired: int
    objects_deleted: int
    rows_cleaned: int
    deleted: bool


class ImportRetirer:
    def __init__(
        self, *, imports: ImportPort, staging: ImportStagingStore, clock: Clock
    ) -> None:
        self._imports = imports
        self._staging = staging
        self._clock = clock

    async def run(self, *, after_restore: bool = False, dry_run: bool = False) -> RetireReport:
        """`dry_run` reaches here too.

        Not obvious, and worth the parameter: `media-gc --dry-run` prints
        "nothing was deleted", and a retirement that expired rows and
        deleted staged uploads anyway would make that line a lie about the
        most destructive half of the command — the half that removes the
        only copy of an upload an admin may still want to confirm.
        """
        now = self._clock.now()
        if dry_run:
            would_expire = await self._imports.count_expirable(
                now, all_unconfirmed=after_restore
            )
            return RetireReport(
                expired=would_expire,
                objects_deleted=len(await self._imports.retirable_staged()),
                rows_cleaned=0,
                deleted=False,
            )

        expired = await self._imports.mark_expired(now, all_unconfirmed=after_restore)
        deleted = 0
        cleaned = 0
        # Every row that still owns a staged object, whatever put it in
        # that state: expired just now, expired by an earlier run that
        # crashed, or confirmed and no longer needing its upload.
        for import_id, staged_key in await self._imports.retirable_staged():
            await self._staging.delete(staged_key)
            deleted += 1
            await self._imports.mark_cleaned(import_id)
            cleaned += 1
        return RetireReport(
            expired=expired, objects_deleted=deleted, rows_cleaned=cleaned, deleted=True
        )
```

...and the matching `ImportPort` methods, implemented in `db/repositories/imports.py`:

```python
    async def count_expirable(self, now: datetime, *, all_unconfirmed: bool) -> int:
        """What `mark_expired` would touch. Read-only, for `--dry-run`."""
        async with self._sessionmaker() as session:
            statement = select(func.count()).select_from(QuestionImport).where(
                QuestionImport.status == ImportStatus.VALIDATED.value
            )
            if not all_unconfirmed:
                statement = statement.where(QuestionImport.expires_at < now)
            return (await session.execute(statement)).scalar_one()

    async def mark_expired(self, now: datetime, *, all_unconfirmed: bool) -> int:
        async with self._sessionmaker() as session, session.begin():
            statement = (
                update(QuestionImport)
                .where(QuestionImport.status == ImportStatus.VALIDATED.value)
                .values(status=ImportStatus.EXPIRED.value)
                .returning(QuestionImport.id)
            )
            if not all_unconfirmed:
                statement = statement.where(QuestionImport.expires_at < now)
            return len((await session.execute(statement)).scalars().all())

    async def retirable_staged(self) -> tuple[tuple[str, str], ...]:
        async with self._sessionmaker() as session:
            rows = (
                await session.execute(
                    select(QuestionImport.id, QuestionImport.staged_key).where(
                        QuestionImport.staged_key.is_not(None),
                        QuestionImport.status.in_(
                            (ImportStatus.EXPIRED.value, ImportStatus.CONFIRMED.value)
                        ),
                    )
                )
            ).all()
        return tuple((row[0], row[1]) for row in rows)

    async def mark_cleaned(self, import_id: str) -> None:
        """`confirmed` stays `confirmed` — §9.3 keeps that row as an audit
        trail and only drops its `staged_key`. Only an `expired` row
        becomes `cleaned`."""
        async with self._sessionmaker() as session, session.begin():
            row = await session.get(QuestionImport, import_id)
            if row is None:
                return
            row.staged_key = None
            if row.status == ImportStatus.EXPIRED.value:
                row.status = ImportStatus.CLEANED.value
```

Create `backend/src/triviador/media/gc.py`:

```python
"""§10.4's asset sweep. Two passes, and the ordering each one needs.

**Recorded assets: rows first, objects second.** `claim_unreferenced`
deletes the rows inside one transaction that holds `FOR UPDATE` on each
of them and re-checks the references under that lock (see its docstring —
the lock is what a concurrent question insert collides with). Only then
are the objects deleted. A crash in between leaves an object with no row,
which the orphan pass collects next time; the opposite order would leave
a question rendering a blob that is gone.

**Orphans: old ones only.** §10.3 says "a failed transaction leaves an
unreferenced blob, which `media-gc` removes safely" — but an object with
no row is *also* what an upload looks like for the few milliseconds
between its `put` and its `INSERT`. Age is the only thing that tells the
two apart, so anything younger than the grace period is left alone. The
upload path's `repair_blob` covers the residue (Decision 9).

**`--dry-run` mutates nothing at all.** Not the objects, not the rows,
and — in `cli.py` — not the import retirement either.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta

from triviador.services.admin import MediaAssetPort
from triviador.services.storage import MediaStore


@dataclass(frozen=True)
class GcReport:
    unreferenced: tuple[str, ...]
    orphan_objects: tuple[str, ...]
    skipped_young: int
    deleted: bool


class MediaCollector:
    def __init__(
        self, *, assets: MediaAssetPort, store: MediaStore, grace: timedelta
    ) -> None:
        self._assets = assets
        self._store = store
        self._grace = grace

    async def run(self, *, now: datetime, dry_run: bool = False) -> GcReport:
        # Listed *before* anything is deleted, so an asset collected by
        # this run is not also reported as an orphan by it.
        listed = await self._store.list_objects()
        known = await self._assets.all_storage_keys()
        cutoff = now - self._grace
        candidates = [o for o in listed if o.key not in known]
        orphans = tuple(sorted(o.key for o in candidates if o.last_modified <= cutoff))
        skipped = len(candidates) - len(orphans)

        if dry_run:
            return GcReport(
                unreferenced=tuple(a.asset_id for a in await self._assets.unreferenced()),
                orphan_objects=orphans,
                skipped_young=skipped,
                deleted=False,
            )

        claimed = await self._assets.claim_unreferenced()
        for asset in claimed:
            await self._store.delete(asset.storage_key)
        for key in orphans:
            await self._store.delete(key)

        return GcReport(
            unreferenced=tuple(a.asset_id for a in claimed),
            orphan_objects=orphans,
            skipped_young=skipped,
            deleted=True,
        )
```

- [ ] **Step 6: Wire the command**

In `backend/src/triviador/cli.py`, add a fourth command:

```python
async def _media_gc_command(args: argparse.Namespace) -> int:
    """Rare and destructive, so it is a command and not a screen (§10.4) —
    and it prints what it did, because an operator running this at 2 a.m.
    needs to be able to tell "nothing to collect" from "did not run"."""
    settings = get_settings()
    async with engine_for(settings.database_url) as engine:
        sessionmaker = sessionmaker_for(engine)
        staging = S3ImportStagingStore(
            endpoint_url=settings.s3_endpoint_url,
            region=settings.s3_region,
            access_key_id=settings.s3_access_key_id,
            secret_access_key=settings.s3_secret_access_key.get_secret_value(),
            bucket=settings.staging_bucket,
        )
        media = S3MediaStore(
            endpoint_url=settings.s3_endpoint_url,
            region=settings.s3_region,
            access_key_id=settings.s3_access_key_id,
            secret_access_key=settings.s3_secret_access_key.get_secret_value(),
            bucket=settings.media_bucket,
        )
        # Imports first: retiring a staged upload can only ever *reduce*
        # what the media sweep has to consider, and running the sweep
        # first would leave every just-expired object for the next run.
        clock = SystemClock()
        retired = await ImportRetirer(
            imports=QuestionImportRepository(sessionmaker),
            staging=staging,
            clock=clock,
        ).run(after_restore=args.after_restore, dry_run=args.dry_run)
        collected = await MediaCollector(
            assets=MediaAssetRepository(sessionmaker),
            store=media,
            grace=timedelta(minutes=settings.media_gc_grace_minutes),
        ).run(now=clock.now(), dry_run=args.dry_run)

    verb = "would expire" if args.dry_run else "expired"
    print(f"imports {verb} {retired.expired}, staged objects {retired.objects_deleted}")
    print(
        f"unreferenced assets {len(collected.unreferenced)}, "
        f"orphan objects {len(collected.orphan_objects)} "
        f"({collected.skipped_young} too recent to touch)"
    )
    if args.dry_run:
        print("dry run: nothing was deleted")
    return 0
```

...registered as:

```python
    gc = commands.add_parser("media-gc")
    gc.add_argument("--dry-run", action="store_true")
    gc.add_argument(
        "--after-restore",
        action="store_true",
        help=(
            "expire every unconfirmed import regardless of its expiry: staging "
            "is not backed up (§10.9), so after a restore their uploads are gone"
        ),
    )
```

...and dispatched in `main` before the `admin-create` fallthrough.

- [ ] **Step 7: Run everything and commit**

Run: `cd backend && uv run pytest -q && uv run mypy && uv run ruff check .`

```bash
git add backend/src/triviador backend/tests
git commit -m "feat(admin): media-gc — retirable staging, two-way reference check, orphan sweep"
```

---

## Task 10: Invites

**Files:**
- Modify: `backend/src/triviador/db/repositories/auth.py`, `backend/src/triviador/services/admin.py`
- Create: `backend/src/triviador/api/schemas/admin/invites.py`, `backend/src/triviador/api/http/admin/invites.py`
- Test: `backend/tests/api/test_admin_invites.py`, `backend/tests/db/test_admin_repositories.py` (append)

**Interfaces:**
- Produces: `services.admin.InviteRecord(invite_id, status, expires_at, used_by)` — no `created_at`: Spec 1 §7's `invite_codes` schema has none, §10.5 does not ask for one, and it would reach no response, `InviteAdminPort.issue(count, expires_at, created_by) -> tuple[IssuedInvite, ...]`, `.list() -> tuple[InviteRecord, ...]`, `.revoke(invite_id, at) -> bool`; `POST /api/admin/invites`, `GET /api/admin/invites`, `POST /api/admin/invites/{id}/revoke`

- [ ] **Step 1: Write the failing tests**

Create `backend/tests/api/test_admin_invites.py`:

```python
import httpx
import pytest

pytestmark = pytest.mark.asyncio


async def test_a_player_cannot_issue_invites(signed_in: httpx.AsyncClient) -> None:
    assert (await signed_in.post("/api/admin/invites", json={"count": 1})).status_code == 403


async def test_issuing_returns_the_codes_exactly_once(admin_client: httpx.AsyncClient) -> None:
    """`invite_codes.code_hash` is a SHA-256 (Plan 3): the plaintext exists
    only in this response. Listing them later returns status, never a
    code — a list endpoint that could re-read them would make the hash
    decorative."""
    issued = await admin_client.post("/api/admin/invites", json={"count": 3, "expires_in_hours": 48})
    assert issued.status_code == 201
    codes = [item["code"] for item in issued.json()]
    assert len(set(codes)) == 3

    listed = await admin_client.get("/api/admin/invites")
    assert listed.status_code == 200
    assert all("code" not in item for item in listed.json())
    assert {item["status"] for item in listed.json()} == {"pending"}


async def test_an_issued_code_can_be_redeemed(admin_client: httpx.AsyncClient,
                                              client: httpx.AsyncClient) -> None:
    """The end-to-end fact this route exists for: `POST /api/auth/redeem`
    is public and takes exactly what was printed here."""
    code = (await admin_client.post("/api/admin/invites", json={"count": 1})).json()[0]["code"]
    redeemed = await client.post(
        "/api/auth/redeem",
        json={"code": code, "username": "newcomer", "password": "correct horse",
              "display_name": "Newcomer"},
    )
    assert redeemed.status_code == 201


async def test_a_revoked_code_cannot_be_redeemed(admin_client: httpx.AsyncClient,
                                                 client: httpx.AsyncClient) -> None:
    issued = (await admin_client.post("/api/admin/invites", json={"count": 1})).json()[0]
    assert (await admin_client.post(f"/api/admin/invites/{issued['id']}/revoke")).status_code == 200
    redeemed = await client.post(
        "/api/auth/redeem",
        json={"code": issued["code"], "username": "late", "password": "correct horse",
              "display_name": "Late"},
    )
    assert redeemed.status_code == 401
    assert redeemed.json()["code"] == "invite_invalid"


async def test_revoking_twice_is_not_an_error(admin_client: httpx.AsyncClient) -> None:
    issued = (await admin_client.post("/api/admin/invites", json={"count": 1})).json()[0]
    await admin_client.post(f"/api/admin/invites/{issued['id']}/revoke")
    second = await admin_client.post(f"/api/admin/invites/{issued['id']}/revoke")
    assert second.status_code == 200
    assert second.json()["status"] == "revoked"


async def test_the_count_is_bounded(admin_client: httpx.AsyncClient) -> None:
    assert (await admin_client.post("/api/admin/invites", json={"count": 0})).status_code == 422
    assert (await admin_client.post("/api/admin/invites", json={"count": 501})).status_code == 422
```

- [ ] **Step 2: Run it and watch it fail**

Run: `cd backend && uv run pytest tests/api/test_admin_invites.py -q`
Expected: FAIL — 404.

- [ ] **Step 3: Repository, schema, route**

Append to `backend/src/triviador/db/repositories/auth.py`, inside `InviteRepository`:

```python
    async def issue(
        self, *, count: int, expires_at: datetime, created_by: UserId
    ) -> tuple[tuple[str, str], ...]:
        """`(invite_id, code)` pairs — the only moment the plaintext exists.

        Generated with `new_token()`, the same 32-byte `secrets` source as
        a session token, and stored as `token_digest(code)`: an invite that
        can be read back out of the database is a credential sitting in a
        backup.
        """
        issued: list[tuple[str, str]] = []
        async with self._sessionmaker() as db, db.begin():
            for _ in range(count):
                code = new_token()
                invite = InviteCode(
                    code_hash=token_digest(code),
                    created_by=created_by,
                    expires_at=expires_at,
                )
                db.add(invite)
                await db.flush()
                issued.append((invite.id, code))
        return tuple(issued)

    async def list_all(self, *, now: datetime) -> tuple[InviteRecord, ...]:
        """Status is derived, never stored: `used_by`, `revoked_at` and
        `expires_at` already say everything, and a fourth column would be
        a copy of them that can disagree."""
        async with self._sessionmaker() as db:
            rows = (
                await db.execute(select(InviteCode).order_by(InviteCode.expires_at.desc()))
            ).scalars().all()
        return tuple(
            InviteRecord(
                invite_id=row.id,
                status=_invite_status(row, now=now),
                expires_at=row.expires_at,
                used_by=row.used_by,
            )
            for row in rows
        )

    async def revoke(self, invite_id: str, *, at: datetime) -> bool:
        async with self._sessionmaker() as db, db.begin():
            row = await db.get(InviteCode, invite_id)
            if row is None:
                return False
            if row.revoked_at is None:
                row.revoked_at = at
            return True
```

...with a module-level:

```python
def _invite_status(row: InviteCode, *, now: datetime) -> str:
    if row.used_by is not None:
        return "used"
    if row.revoked_at is not None:
        return "revoked"
    return "expired" if row.expires_at <= now else "pending"
```

Add `InviteRecord` and `InviteAdminPort` to `services/admin.py`; create `api/schemas/admin/invites.py` with

```python
class IssueInvitesRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    count: int = Field(ge=1, le=500)
    expires_in_hours: int = Field(default=168, ge=1, le=8760)


class IssuedInvite(BaseModel):
    """Carries `code`. This model appears in exactly one response and
    never in a listing — see `InviteView`."""

    model_config = ConfigDict(extra="forbid")

    id: str
    code: str
    expires_at: datetime


class InviteView(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    status: Literal["pending", "used", "revoked", "expired"]
    expires_at: datetime
    used_by: str | None
```

...and `backend/src/triviador/api/http/admin/invites.py`:

```python
"""§10.5's invite half.

The plaintext code exists in exactly one response body, because
`invite_codes` stores only `token_digest(code)` (Plan 3). An admin who
loses the code issues another one — which costs nothing — and a listing
that could show it again would mean the digest was never protecting
anything.
"""

from datetime import timedelta

from fastapi import APIRouter

from triviador.api.deps import AdminPrincipal, Deps
from triviador.api.errors import ApiError, ApiErrorCode
from triviador.api.schemas.admin.invites import InviteView, IssueInvitesRequest, IssuedInvite
from triviador.services.admin import InviteRecord

router = APIRouter(prefix="/invites", tags=["admin"])


def _view(record: InviteRecord) -> InviteView:
    return InviteView(
        id=record.invite_id,
        status=record.status,
        expires_at=record.expires_at,
        used_by=record.used_by,
    )


@router.post("", status_code=201)
async def issue_invites(
    body: IssueInvitesRequest, deps: Deps, principal: AdminPrincipal
) -> list[IssuedInvite]:
    expires_at = deps.clock.now() + timedelta(hours=body.expires_in_hours)
    issued = await deps.invites_admin.issue(
        count=body.count, expires_at=expires_at, created_by=principal.user_id
    )
    return [
        IssuedInvite(id=invite_id, code=code, expires_at=expires_at)
        for invite_id, code in issued
    ]


@router.get("")
async def list_invites(deps: Deps, principal: AdminPrincipal) -> list[InviteView]:
    return [_view(record) for record in await deps.invites_admin.list_all(now=deps.clock.now())]


@router.post("/{invite_id}/revoke")
async def revoke_invite(invite_id: str, deps: Deps, principal: AdminPrincipal) -> InviteView:
    """Idempotent: revoking an already-revoked code answers 200 with the
    same body. An admin clicking twice has not made a mistake worth an
    error, and the second click is indistinguishable from a retry."""
    if not await deps.invites_admin.revoke(invite_id, at=deps.clock.now()):
        raise ApiError(ApiErrorCode.NOT_FOUND, 404, "no such invite")
    records = await deps.invites_admin.list_all(now=deps.clock.now())
    record = next(r for r in records if r.invite_id == invite_id)
    return _view(record)
```

Wire `invites_admin: InviteAdminPort` into `AppDependencies`, `build_dependencies` (the existing `InviteRepository(sessions)` satisfies both ports) and the `deps` fixture, and include the router in `build_admin_router(...)`.

- [ ] **Step 4: Run everything and commit**

Run: `cd backend && uv run pytest -q && uv run mypy && uv run ruff check .`

```bash
git add backend/src/triviador backend/tests
git commit -m "feat(admin): issue, list and revoke invite codes"
```

---

## Task 11: Users — deactivation that logs them out now, and the last admin who cannot be demoted

Spec 1 §10.5's two constraints are both concurrency problems, and the spec says so: "Last-admin protection must be transactional… A `count_admins() == 1` check followed by a separate update lets two admins concurrently demote each other."

**Files:**
- Modify: `backend/src/triviador/db/repositories/auth.py`, `backend/src/triviador/services/admin.py`, `backend/src/triviador/api/errors.py`
- Create: `backend/src/triviador/api/schemas/admin/users.py`, `backend/src/triviador/api/http/admin/users.py`
- Test: `backend/tests/api/test_admin_users.py`, `backend/tests/db/test_admin_repositories.py` (append)

**Interfaces:**
- Produces: `services.admin.UserAdminPort.list()`, `.deactivate(user_id) -> tuple[SessionId, ...] | None`, `.set_role(user_id, role) -> SetRoleOutcome`; `ApiErrorCode.LAST_ADMIN`, `ApiErrorCode.SELF_TARGET`; `GET /api/admin/users`, `POST /api/admin/users/{id}/deactivate`, `POST /api/admin/users/{id}/role`

- [ ] **Step 1: Write the failing tests**

Create `backend/tests/api/test_admin_users.py`:

```python
import httpx
import pytest

from triviador.api.deps import AppDependencies

pytestmark = pytest.mark.asyncio


async def test_a_player_cannot_list_users(signed_in: httpx.AsyncClient) -> None:
    assert (await signed_in.get("/api/admin/users")).status_code == 403


async def test_deactivating_a_user_closes_their_sockets_with_4401(
    admin_client: httpx.AsyncClient, deps: AppDependencies
) -> None:
    """§10.5: "Deactivation kills sessions immediately — precisely why §7
    chose opaque tokens." The REST half revokes; the socket half is
    `Hub.close_sessions`, which Plan 5 built for this caller."""
    response = await admin_client.post("/api/admin/users/u1/deactivate")
    assert response.status_code == 200
    assert response.json()["is_active"] is False
    assert deps.hub.closed == [(("s1",), 4401)]


async def test_a_deactivated_user_cannot_use_their_cookie_again(
    admin_client: httpx.AsyncClient, signed_in: httpx.AsyncClient
) -> None:
    """The session resolver joins `users.is_active` (Plan 5's
    `SessionRepository.resolve`), so this holds even for a request already
    in flight behind the revocation."""
    await admin_client.post("/api/admin/users/u1/deactivate")
    assert (await signed_in.get("/api/auth/me")).status_code == 401


async def test_an_admin_cannot_deactivate_themselves(admin_client: httpx.AsyncClient) -> None:
    response = await admin_client.post("/api/admin/users/admin/deactivate")
    assert response.status_code == 409
    assert response.json()["code"] == "self_target"


async def test_the_last_admin_cannot_be_demoted(admin_client: httpx.AsyncClient) -> None:
    response = await admin_client.post("/api/admin/users/admin/role", json={"role": "player"})
    assert response.status_code == 409
    assert response.json()["code"] == "last_admin"


async def test_promoting_then_demoting_is_allowed(
    admin_client: httpx.AsyncClient, deps: AppDependencies
) -> None:
    assert (
        await admin_client.post("/api/admin/users/u1/role", json={"role": "admin"})
    ).status_code == 200
    demoted = await admin_client.post("/api/admin/users/admin/role", json={"role": "player"})
    assert demoted.status_code == 200
    assert demoted.json()["role"] == "player"


async def test_demotion_also_closes_that_user_s_sockets(
    admin_client: httpx.AsyncClient, deps: AppDependencies
) -> None:
    """A socket opened as an admin keeps its `AuthenticatedPrincipal` for
    the life of the connection (§6.5), so a demotion that left it open
    would leave admin standing behind on a live connection."""
    await admin_client.post("/api/admin/users/u1/role", json={"role": "admin"})
    await admin_client.post("/api/admin/users/u1/role", json={"role": "player"})
    assert (("s1",), 4401) in deps.hub.closed
```

Append the concurrency test to `backend/tests/db/test_admin_repositories.py`:

```python
async def test_two_admins_cannot_demote_each_other_into_an_empty_room(
    sessions, clean_db
) -> None:
    """The exact race §10.5 names. Both transactions see two admins if the
    check is a plain `SELECT count(*)`; the `FOR UPDATE` over every admin
    row serialises them, so the second sees one."""
    import asyncio

    repository = UserAdminRepository(sessions)
    await _seed_user(sessions, "a1")   # role='admin' (the helper's default)
    await _seed_user(sessions, "a2")

    outcomes = await asyncio.gather(
        repository.set_role(UserId("a1"), role=UserRole.PLAYER),
        repository.set_role(UserId("a2"), role=UserRole.PLAYER),
    )
    assert sorted(o.value for o in outcomes) == ["last_admin", "ok"]
    assert await UserRepository(sessions).count_admins() == 1
```

- [ ] **Step 2: Run them and watch them fail**

Run: `cd backend && uv run pytest tests/api/test_admin_users.py -q`
Expected: FAIL — 404.

- [ ] **Step 3: Write the repository**

Append to `backend/src/triviador/db/repositories/auth.py`:

```python
class SetRoleOutcome(StrEnum):
    OK = "ok"
    NOT_FOUND = "not_found"
    LAST_ADMIN = "last_admin"


class UserAdminRepository:
    """Implements `services.admin.UserAdminPort`.

    Separate from `UserRepository`, which is the identity path every
    request touches: the admin surface's methods take locks and are
    allowed to be slow, and mixing them would put a `FOR UPDATE` over the
    whole admin set one autocomplete away from the login path.
    """

    def __init__(self, sessionmaker: async_sessionmaker[AsyncSession]) -> None:
        self._sessionmaker = sessionmaker

    async def list(self) -> tuple[UserRecord, ...]:
        async with self._sessionmaker() as db:
            rows = (await db.execute(select(User).order_by(User.username))).scalars().all()
        return tuple(_to_record(row) for row in rows)

    async def deactivate(self, user_id: UserId, *, at: datetime) -> tuple[SessionId, ...] | None:
        """One transaction: flip the flag and revoke every session, then
        hand the caller the ids so it can close their sockets **after the
        commit** — the same "committed before published" discipline §11.2
        applies to game events.
        """
        async with self._sessionmaker() as db, db.begin():
            user = await db.get(User, user_id, with_for_update=True)
            if user is None:
                return None
            user.is_active = False
            revoked = await db.execute(
                update(Session)
                .where(Session.user_id == user_id, Session.revoked_at.is_(None))
                .values(revoked_at=at)
                .returning(Session.id)
            )
            return tuple(SessionId(i) for i in revoked.scalars().all())

    async def set_role(
        self, user_id: UserId, *, role: UserRole, at: datetime
    ) -> tuple[SetRoleOutcome, tuple[SessionId, ...]]:
        async with self._sessionmaker() as db, db.begin():
            # Lock every active admin row *first*, in one statement. Two
            # concurrent demotions then serialise here rather than both
            # reading a count of two and both writing.
            admins = (
                await db.execute(
                    select(User.id)
                    .where(User.role == str(UserRole.ADMIN), User.is_active)
                    .order_by(User.id)
                    .with_for_update()
                )
            ).scalars().all()
            user = await db.get(User, user_id)
            if user is None:
                return SetRoleOutcome.NOT_FOUND, ()
            if (
                role is UserRole.PLAYER
                and user.role == str(UserRole.ADMIN)
                and len(admins) <= 1
            ):
                return SetRoleOutcome.LAST_ADMIN, ()
            if user.role == str(role):
                return SetRoleOutcome.OK, ()
            user.role = str(role)
            # A live socket carries the principal it authenticated with
            # (§6.5), so a role change has to end the sessions that hold
            # the old one. The user signs in again and gets the new role.
            revoked = await db.execute(
                update(Session)
                .where(Session.user_id == user_id, Session.revoked_at.is_(None))
                .values(revoked_at=at)
                .returning(Session.id)
            )
            return SetRoleOutcome.OK, tuple(SessionId(i) for i in revoked.scalars().all())
```

- [ ] **Step 4: Write the schema and the routes**

`backend/src/triviador/api/schemas/admin/users.py`:

```python
class UserView(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    username: str
    display_name: str
    role: UserRole
    is_active: bool


class SetRoleRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    role: UserRole
```

`backend/src/triviador/api/http/admin/users.py`:

```python
"""§10.5's user half. Two rules, both enforced under a lock, both 409.

`self_target` covers deactivating yourself and demoting yourself; they are
one mistake ("I clicked the wrong row, and the row was mine") and one
recovery ("ask another admin"), so they share a code.
"""

from fastapi import APIRouter

from triviador.api.deps import AdminPrincipal, Deps
from triviador.api.errors import ApiError, ApiErrorCode
from triviador.api.schemas.admin.users import SetRoleRequest, UserView
from triviador.db.repositories.auth import SetRoleOutcome
from triviador.domain.ids import UserId
from triviador.services.identity import UserRecord

router = APIRouter(prefix="/users", tags=["admin"])


def _view(record: UserRecord) -> UserView:
    return UserView(
        id=str(record.user_id),
        username=record.username,
        display_name=record.display_name,
        role=record.role,
        is_active=record.is_active,
    )


@router.get("")
async def list_users(deps: Deps, principal: AdminPrincipal) -> list[UserView]:
    return [_view(record) for record in await deps.users_admin.list()]


@router.post("/{user_id}/deactivate")
async def deactivate_user(user_id: str, deps: Deps, principal: AdminPrincipal) -> UserView:
    if user_id == str(principal.user_id):
        raise ApiError(ApiErrorCode.SELF_TARGET, 409, "you cannot deactivate your own account")
    revoked = await deps.users_admin.deactivate(UserId(user_id), at=deps.clock.now())
    if revoked is None:
        raise ApiError(ApiErrorCode.NOT_FOUND, 404, "no such user")
    # After the commit, never inside it: a socket closed for a
    # transaction that then rolled back is a player kicked out of a live
    # game for nothing.
    deps.hub.close_sessions(revoked, 4401)
    record = await deps.users_admin.get(UserId(user_id))
    assert record is not None
    return _view(record)


@router.post("/{user_id}/role")
async def set_role(
    user_id: str, body: SetRoleRequest, deps: Deps, principal: AdminPrincipal
) -> UserView:
    outcome, revoked = await deps.users_admin.set_role(
        UserId(user_id), role=body.role, at=deps.clock.now()
    )
    if outcome is SetRoleOutcome.NOT_FOUND:
        raise ApiError(ApiErrorCode.NOT_FOUND, 404, "no such user")
    if outcome is SetRoleOutcome.LAST_ADMIN:
        raise ApiError(
            ApiErrorCode.LAST_ADMIN, 409, "this is the last administrator; promote another first"
        )
    deps.hub.close_sessions(revoked, 4401)
    record = await deps.users_admin.get(UserId(user_id))
    assert record is not None
    return _view(record)
```

Add `LAST_ADMIN = "last_admin"` and `SELF_TARGET = "self_target"` to `ApiErrorCode`, add `UserAdminPort` (with `get`) to `services/admin.py`, wire `users_admin` into `AppDependencies`/`build_dependencies`/`deps` fixture, and give the api test suite's `Hub` fake (or the real `Hub`, which already records nothing) a `closed` list — use the real `Hub` and assert through a small `RecordingHub` subclass declared in `tests/api/fakes.py`.

- [ ] **Step 5: Run everything and commit**

Run: `cd backend && uv run pytest -q && uv run mypy && uv run ruff check .`

```bash
git add backend/src/triviador backend/tests
git commit -m "feat(admin): user list, deactivation that closes sockets, transactional last-admin rule"
```

---

## Task 12: Presets — CRUD, coverage, and the one route players can see

**Files:**
- Modify: `backend/src/triviador/db/repositories/presets.py`, `backend/src/triviador/services/admin.py`, `backend/src/triviador/api/errors.py` (`DEFAULT_PRESET`)
- Create: `backend/src/triviador/api/schemas/admin/presets.py`, `backend/src/triviador/api/http/admin/presets.py`
- Create: `backend/src/triviador/api/http/presets.py` (public), `backend/src/triviador/api/schemas/presets.py`
- Modify: `backend/src/triviador/api/app.py` (include the public router), `deps.py`
- Test: `backend/tests/api/test_admin_presets.py`, `backend/tests/api/test_presets.py`, `backend/tests/db/test_presets.py` (append)

**Interfaces:**
- Produces:
  - `services.admin.PresetAdminRecord(preset_id, name, rules, is_default, is_active)` and `DeactivateOutcome{OK, NOT_FOUND, IS_DEFAULT}`
  - `services.admin.PresetAdminPort.list_all()`, `.get_including_retired(preset_id)`, `.create(name, rules, is_default)`, `.update(preset_id, name, rules, is_default)`, `.deactivate(preset_id)`
  - **The admin single-item read must not filter on `is_active`.** `PresetPort.get` does — a player must never start a game on a retired preset — but one `PresetRepository` instance satisfies both ports, so the admin lookup needs its own name. Without it, `GET /api/admin/presets/{id}` 404s for exactly the retired presets `list_all` shows, and the `is_active` field on the admin record has no reachable detail view to be rendered in.
  - `services.admin.QuestionAdminPort.active_counts() -> dict[str, int]`
  - `services.ports.PresetPort.list_active() -> tuple[PresetRecord, ...]` (the public read)
  - `GET /api/presets` → `list[PresetSummary]` (any signed-in user)
  - `GET|POST /api/admin/presets`, `GET|PATCH|DELETE /api/admin/presets/{id}`, `GET /api/admin/presets/{id}/coverage`
  - `ApiErrorCode.DEFAULT_PRESET`

- [ ] **Step 1: Write the failing tests**

Create `backend/tests/api/test_admin_presets.py`:

```python
import httpx
import pytest

pytestmark = pytest.mark.asyncio

QUICK = {
    "name": "Quick",
    "is_default": False,
    "rules": {
        "player_count": 3,
        "expansion_rounds": 2,
        "battle_rounds": 2,
        "base_hp": 3,
        "answer_timeout_ms": 20000,
        "pick_timeout_ms": 15000,
        "warmup_ms": 5000,
        "claims_by_rank": [2, 1, 0],
        "pts_base": 1000,
        "pts_territory": 200,
        "pts_conquered": 400,
        "pts_defense": 100,
    },
}


async def test_a_player_cannot_write_presets(signed_in: httpx.AsyncClient) -> None:
    assert (await signed_in.post("/api/admin/presets", json=QUICK)).status_code == 403


async def test_create_and_list(admin_client: httpx.AsyncClient) -> None:
    created = await admin_client.post("/api/admin/presets", json=QUICK)
    assert created.status_code == 201
    listed = (await admin_client.get("/api/admin/presets")).json()
    assert {p["name"] for p in listed} >= {"Quick", "Default"}


async def test_invalid_rules_are_rejected_with_the_domain_s_own_reasons(
    admin_client: httpx.AsyncClient
) -> None:
    """`validate_rules` is the single source of what a legal ruleset is
    (Plan 2). Re-stating its rules in a Pydantic model would be a second
    copy that drifts; the route calls it and reports what it says."""
    body = {**QUICK, "rules": {**QUICK["rules"], "claims_by_rank": [2, 1]}}
    response = await admin_client.post("/api/admin/presets", json=body)
    assert response.status_code == 422
    assert "claims_by_rank" in response.json()["message"]


async def test_making_a_preset_default_demotes_the_previous_one(
    admin_client: httpx.AsyncClient
) -> None:
    """`uq_rule_presets_single_default` is a partial unique index (Plan 3):
    without demoting the old default in the same transaction, this is an
    IntegrityError, i.e. a 503 on a legitimate action."""
    created = (
        await admin_client.post("/api/admin/presets", json={**QUICK, "is_default": True})
    ).json()
    listed = (await admin_client.get("/api/admin/presets")).json()
    defaults = [p["id"] for p in listed if p["is_default"]]
    assert defaults == [created["id"]]


async def test_the_default_cannot_be_cleared_by_a_patch(admin_client: httpx.AsyncClient) -> None:
    """The database enforces *at most* one default; "never zero" is ours,
    and `deactivate` is not the only door into it. Clearing the flag here
    would leave `POST /api/games` answering `no_default_preset` to every
    player until someone noticed."""
    default = next(p for p in (await admin_client.get("/api/admin/presets")).json()
                   if p["is_default"])
    response = await admin_client.patch(
        f"/api/admin/presets/{default['id']}",
        json={"name": default["name"], "is_default": False, "rules": default["rules"]},
    )
    assert response.status_code == 409
    assert response.json()["code"] == "default_preset"


async def test_a_retired_preset_cannot_be_promoted_to_default(
    admin_client: httpx.AsyncClient
) -> None:
    """`get_default()` filters on `is_active`, so an inactive default is a
    default nothing can read — the same outage as having none."""
    created = (await admin_client.post("/api/admin/presets", json=QUICK)).json()
    await admin_client.delete(f"/api/admin/presets/{created['id']}")
    response = await admin_client.patch(
        f"/api/admin/presets/{created['id']}",
        json={**QUICK, "is_default": True},
    )
    assert response.status_code == 409
    assert response.json()["code"] == "default_preset"


async def test_the_default_preset_cannot_be_deleted(admin_client: httpx.AsyncClient) -> None:
    """Spec 1B §6.1: DELETE is a soft deactivation and returns 409 for the
    default — "never zero defaults" is application logic the database
    cannot express."""
    default = next(p for p in (await admin_client.get("/api/admin/presets")).json()
                   if p["is_default"])
    response = await admin_client.delete(f"/api/admin/presets/{default['id']}")
    assert response.status_code == 409
    assert response.json()["code"] == "default_preset"


async def test_deleting_a_preset_is_a_soft_deactivation(admin_client: httpx.AsyncClient) -> None:
    created = (await admin_client.post("/api/admin/presets", json=QUICK)).json()
    assert (await admin_client.delete(f"/api/admin/presets/{created['id']}")).status_code == 204
    listed = (await admin_client.get("/api/admin/presets")).json()
    assert [p["is_active"] for p in listed if p["id"] == created["id"]] == [False]


async def test_coverage_reports_need_and_bank_per_kind(admin_client: httpx.AsyncClient) -> None:
    """§10.6's table, as numbers. `required_question_budget` is the domain
    function `StartGame` itself uses, so the informative answer here and
    the authoritative one at start time cannot disagree about the need —
    only about the bank, which is the point."""
    created = (await admin_client.post("/api/admin/presets", json=QUICK)).json()
    coverage = (await admin_client.get(f"/api/admin/presets/{created['id']}/coverage")).json()
    assert coverage["required"] == {"numeric": 9, "multiple_choice": 6}
    assert set(coverage["bank"]) == {"numeric", "multiple_choice"}
    assert isinstance(coverage["sufficient"], bool)
```

Create `backend/tests/api/test_presets.py`:

```python
import httpx
import pytest

pytestmark = pytest.mark.asyncio


async def test_an_anonymous_visitor_gets_401(client: httpx.AsyncClient) -> None:
    assert (await client.get("/api/presets")).status_code == 401


async def test_a_player_sees_active_presets_without_admin(signed_in: httpx.AsyncClient) -> None:
    """The deviation this plan states in Decision 1: without it, `POST
    /api/games` accepts a `preset_id` no player could ever learn."""
    response = await signed_in.get("/api/presets")
    assert response.status_code == 200
    body = response.json()
    assert body and {"id", "name", "is_default", "rules"} <= set(body[0])


async def test_a_deactivated_preset_is_not_listed(
    signed_in: httpx.AsyncClient, admin_client: httpx.AsyncClient
) -> None:
    created = (await admin_client.post("/api/admin/presets", json=QUICK)).json()
    await admin_client.delete(f"/api/admin/presets/{created['id']}")
    assert created["id"] not in {p["id"] for p in (await signed_in.get("/api/presets")).json()}
```

(with `QUICK` imported from `tests.api.test_admin_presets`).

- [ ] **Step 2: Run them and watch them fail**

Run: `cd backend && uv run pytest tests/api/test_admin_presets.py tests/api/test_presets.py -q`
Expected: FAIL — 404 on both surfaces.

- [ ] **Step 3: Extend the repository**

Append to `backend/src/triviador/db/repositories/presets.py` (and change its module docstring's "CRUD is Plan 7" to say where the CRUD now is):

```python
    async def list_active(self) -> tuple[PresetRecord, ...]:
        """The public read (`GET /api/presets`). Active only — a retired
        preset must not be selectable, and `is_active` is exactly what
        retirement means here."""
        async with self._sessionmaker() as session:
            rows = (
                await session.execute(
                    select(RulePreset).where(RulePreset.is_active).order_by(RulePreset.name)
                )
            ).scalars().all()
        return tuple(PresetRecord(r.id, r.name, _to_rules(r.rules)) for r in rows)

    async def list_all(self) -> tuple[PresetAdminRecord, ...]:
        """The admin read: retired presets included, `is_default` and
        `is_active` exposed, because retiring and promoting are exactly
        what this screen does."""
        async with self._sessionmaker() as session:
            rows = (
                await session.execute(select(RulePreset).order_by(RulePreset.name))
            ).scalars().all()
        return tuple(
            PresetAdminRecord(r.id, r.name, _to_rules(r.rules), r.is_default, r.is_active)
            for r in rows
        )

    async def create(
        self, *, name: str, rules: GameRules, is_default: bool
    ) -> PresetAdminRecord:
        async with self._sessionmaker() as session, session.begin():
            if is_default:
                await self._clear_default(session)
            preset = RulePreset(
                id=str(uuid4()),
                name=name,
                rules=asdict(rules),
                is_default=is_default,
                version=1,
                is_active=True,
            )
            session.add(preset)
        return PresetAdminRecord(preset.id, name, rules, is_default, True)

    async def update(
        self, preset_id: str, *, name: str, rules: GameRules, is_default: bool
    ) -> tuple[UpdateOutcome, PresetAdminRecord | None]:
        """Editing a preset does not touch a running game: `games.rules`
        holds a frozen copy taken at creation (§6.2), which is why
        `version` is bumped here for the admin screen's benefit and
        nothing else has to be notified.

        **Two default transitions are refused, both inside this
        transaction.** The database enforces *at most one* default with a
        partial unique index; "never zero, and never a retired one" is
        application logic, and `deactivate` is not the only door into it:

            default → is_default=false     leaves the system with no
                                           default at all, and
                                           `POST /api/games` with
                                           `preset_id: null` then 409s with
                                           `no_default_preset` for everyone

            retired → is_default=true      makes a default `get_default()`
                                           cannot return, because it filters
                                           on `is_active` — the same outage,
                                           reached from the other side
        """
        async with self._sessionmaker() as session, session.begin():
            row = await session.get(RulePreset, preset_id, with_for_update=True)
            if row is None:
                return UpdateOutcome.NOT_FOUND, None
            if row.is_default and not is_default:
                return UpdateOutcome.WOULD_LEAVE_NO_DEFAULT, None
            if is_default and not row.is_active:
                return UpdateOutcome.RETIRED_CANNOT_BE_DEFAULT, None
            if is_default and not row.is_default:
                await self._clear_default(session)
            row.name = name
            row.rules = asdict(rules)
            row.is_default = is_default
            row.version = row.version + 1
            return UpdateOutcome.OK, PresetAdminRecord(
                row.id, name, rules, is_default, row.is_active
            )

    async def deactivate(self, preset_id: str) -> DeactivateOutcome:
        async with self._sessionmaker() as session, session.begin():
            row = await session.get(RulePreset, preset_id, with_for_update=True)
            if row is None:
                return DeactivateOutcome.NOT_FOUND
            if row.is_default:
                # "Exactly one default" is a database constraint in one
                # direction only (at most one); "never zero" is here.
                return DeactivateOutcome.IS_DEFAULT
            row.is_active = False
            return DeactivateOutcome.OK

    @staticmethod
    async def _clear_default(session: AsyncSession) -> None:
        """Demote inside the same transaction as the promotion.

        `uq_rule_presets_single_default` is a partial unique index, so two
        rows with `is_default` cannot coexist even momentarily — doing this
        in a second transaction would fail half the time and corrupt the
        invariant the other half.
        """
        await session.execute(
            update(RulePreset).where(RulePreset.is_default).values(is_default=False)
        )
```

...with `PresetAdminRecord`, `DeactivateOutcome` and

```python
class UpdateOutcome(StrEnum):
    OK = "ok"
    NOT_FOUND = "not_found"
    WOULD_LEAVE_NO_DEFAULT = "would_leave_no_default"
    RETIRED_CANNOT_BE_DEFAULT = "retired_cannot_be_default"
```

in `services/admin.py`, and `from dataclasses import asdict, fields`, `from uuid import uuid4`, `from sqlalchemy import select, update` imported.

Coverage needs the bank counts; add to `QuestionAdminPort` and `QuestionAdminRepository`:

```python
    async def active_counts(self) -> dict[str, int]:
        """Active questions per kind. The same shape `seed-questions`
        prints, computed the same way — one query, grouped."""
        async with self._sessionmaker() as session:
            rows = await session.execute(
                select(Question.kind, func.count())
                .where(Question.is_active.is_(True))
                .group_by(Question.kind)
            )
            counts = {kind.value: 0 for kind in QuestionKind}
            for kind, count in rows.all():
                counts[kind] = count
            return counts
```

- [ ] **Step 4: Write the schemas and both routers**

`backend/src/triviador/api/schemas/presets.py` (public):

```python
class RulesView(BaseModel):
    """`GameRules`, field for field. Written out rather than generated
    from the dataclass so the contract is reviewable in the diff — this
    model is what the lobby's rules readout renders."""

    model_config = ConfigDict(extra="forbid")

    player_count: int
    expansion_rounds: int
    battle_rounds: int
    base_hp: int
    answer_timeout_ms: int
    pick_timeout_ms: int
    warmup_ms: int
    claims_by_rank: list[int]
    pts_base: int
    pts_territory: int
    pts_conquered: int
    pts_defense: int


class PresetSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    name: str
    is_default: bool
    rules: RulesView
```

`backend/src/triviador/api/http/presets.py`:

```python
"""`GET /api/presets` — the one preset route that is not admin-only.

Spec 1B §6.1 lists presets under `/api/admin` alone. This route is a
deliberate addition (Plan 7A, Decision 1): `POST /api/games` takes a
`preset_id`, and without a way to list them the parameter is unusable and
every game runs whatever "default" currently means. Read-only, active
presets only, any signed-in user — the same standing `GET /api/maps` has.
"""

from dataclasses import asdict

from fastapi import APIRouter

from triviador.api.deps import Deps, Principal
from triviador.api.schemas.presets import PresetSummary, RulesView

router = APIRouter(prefix="/api/presets", tags=["presets"])


@router.get("")
async def list_presets(deps: Deps, principal: Principal) -> list[PresetSummary]:
    default = await deps.presets.get_default()
    return [
        PresetSummary(
            id=record.preset_id,
            name=record.name,
            is_default=default is not None and default.preset_id == record.preset_id,
            rules=RulesView(**asdict(record.rules)),
        )
        for record in await deps.presets.list_active()
    ]
```

`backend/src/triviador/api/schemas/admin/presets.py` adds `PresetDetail` (`PresetSummary` plus `is_active`), `PresetWriteRequest` (`name`, `is_default`, `rules: RulesView`) and:

```python
class PresetCoverage(BaseModel):
    """§10.6's readout, and its honesty in a field.

    `informative` is `True` and always will be: between reading this and
    starting a game an admin can deactivate a question, so the
    authoritative check is the one `StartGame` makes in the transaction
    that draws the pool. The field exists so the screen has something to
    render that sentence from rather than inventing it.
    """

    model_config = ConfigDict(extra="forbid")

    required: dict[str, int]
    bank: dict[str, int]
    sufficient: bool
    informative: bool = True
```

`backend/src/triviador/api/http/admin/presets.py`:

```python
"""§10.6's CRUD, and §6.1's soft delete.

Editing a preset never touches a running game: `games.rules` holds a
frozen copy taken at creation (§6.2). The admin screen says so in a
sentence; this module is where that sentence is true.
"""

from dataclasses import asdict

from fastapi import APIRouter, Response

from triviador.api.deps import AdminPrincipal, Deps
from triviador.api.errors import ApiError, ApiErrorCode
from triviador.api.schemas.admin.presets import (
    PresetCoverage,
    PresetDetail,
    PresetWriteRequest,
)
from triviador.api.schemas.presets import RulesView
from triviador.domain.game.rules import GameRules, required_question_budget, validate_rules
from triviador.services.admin import DeactivateOutcome, PresetAdminRecord

router = APIRouter(prefix="/presets", tags=["admin"])


def _detail(record: PresetAdminRecord) -> PresetDetail:
    return PresetDetail(
        id=record.preset_id,
        name=record.name,
        is_default=record.is_default,
        is_active=record.is_active,
        rules=RulesView(**asdict(record.rules)),
    )


def _rules(view: RulesView) -> GameRules:
    """`validate_rules` is the single definition of a legal ruleset
    (Plan 2). Restating its bounds in a Pydantic model would be a second
    copy, and the copy is the one that would drift."""
    rules = GameRules(**{**view.model_dump(), "claims_by_rank": tuple(view.claims_by_rank)})
    problems = validate_rules(rules)
    if problems:
        raise ApiError(ApiErrorCode.VALIDATION_FAILED, 422, "; ".join(problems))
    return rules


@router.get("")
async def list_presets(deps: Deps, principal: AdminPrincipal) -> list[PresetDetail]:
    return [_detail(record) for record in await deps.presets_admin.list_all()]


@router.post("", status_code=201)
async def create_preset(
    body: PresetWriteRequest, deps: Deps, principal: AdminPrincipal
) -> PresetDetail:
    record = await deps.presets_admin.create(
        name=body.name, rules=_rules(body.rules), is_default=body.is_default
    )
    return _detail(record)


@router.get("/{preset_id}")
async def get_preset(preset_id: str, deps: Deps, principal: AdminPrincipal) -> PresetDetail:
    record = await deps.presets_admin.get(preset_id)
    if record is None:
        raise ApiError(ApiErrorCode.NOT_FOUND, 404, "no such preset")
    return _detail(record)


@router.patch("/{preset_id}")
async def update_preset(
    preset_id: str, body: PresetWriteRequest, deps: Deps, principal: AdminPrincipal
) -> PresetDetail:
    outcome, record = await deps.presets_admin.update(
        preset_id, name=body.name, rules=_rules(body.rules), is_default=body.is_default
    )
    if outcome is UpdateOutcome.NOT_FOUND:
        raise ApiError(ApiErrorCode.NOT_FOUND, 404, "no such preset")
    if outcome is UpdateOutcome.WOULD_LEAVE_NO_DEFAULT:
        raise ApiError(
            ApiErrorCode.DEFAULT_PRESET,
            409,
            "this is the default preset; make another one default instead of clearing this one",
        )
    if outcome is UpdateOutcome.RETIRED_CANNOT_BE_DEFAULT:
        raise ApiError(
            ApiErrorCode.DEFAULT_PRESET,
            409,
            "a retired preset cannot be the default; reactivate it first",
        )
    assert record is not None  # every other outcome carries one
    return _detail(record)


@router.delete("/{preset_id}", status_code=204, response_class=Response)
async def deactivate_preset(preset_id: str, deps: Deps, principal: AdminPrincipal) -> Response:
    outcome = await deps.presets_admin.deactivate(preset_id)
    if outcome is DeactivateOutcome.NOT_FOUND:
        raise ApiError(ApiErrorCode.NOT_FOUND, 404, "no such preset")
    if outcome is DeactivateOutcome.IS_DEFAULT:
        raise ApiError(
            ApiErrorCode.DEFAULT_PRESET,
            409,
            "this is the default preset; make another one default first",
        )
    return Response(status_code=204)
```

The two helpers above are the whole conversion story — `RulesView` → `GameRules` on the way in,
`asdict` on the way out. Finally, appended to the same module, §10.6's readout:

```python
@router.get("/{preset_id}/coverage")
async def preset_coverage(
    preset_id: str, deps: Deps, principal: AdminPrincipal
) -> PresetCoverage:
    record = await deps.presets_admin.get(preset_id)
    if record is None:
        raise ApiError(ApiErrorCode.NOT_FOUND, 404, "no such preset")
    budget = required_question_budget(record.rules)
    bank = await deps.questions_admin.active_counts()
    required = {"numeric": budget.numeric, "multiple_choice": budget.multiple_choice}
    return PresetCoverage(
        required=required,
        bank=bank,
        sufficient=all(bank.get(kind, 0) >= need for kind, need in required.items()),
    )
```

Add `DEFAULT_PRESET = "default_preset"` to `ApiErrorCode`, include `presets.router` in `build_admin_router(...)`, include the public `presets.router` in `create_app`, and extend `PresetPort` in `services/ports.py` with `list_active`.

- [ ] **Step 5: Run everything and commit**

Run: `cd backend && uv run pytest -q && uv run mypy && uv run ruff check .`

```bash
git add backend/src/triviador backend/tests
git commit -m "feat(admin): preset CRUD with coverage, and a public preset list for the lobby"
```

---

## Task 13: The contracts

Plan 5 decided how this works and left the file for this plan: "`admin.ts` is not generated by this plan. §7 lists four generated modules; admin DTOs do not exist until Plan 7. `scripts/codegen.mjs` generates from what `contracts/` actually contains, so Plan 7 adds `contracts/admin.schema.json` and gets `admin.ts` with no change to the script."

**Files:**
- Modify: `backend/src/triviador/api/contracts.py`
- Modify: `backend/tests/api/test_contracts.py`
- Generated: `contracts/admin.schema.json`, `contracts/errors.json`, `contracts/rest.schema.json`, `contracts/openapi.json`
- Generated: `frontend/src/shared/api/generated/admin.ts`, `.../rest.ts`, `.../errors.ts`

**Interfaces:**
- Produces: `triviador.api.contracts.ADMIN_MODELS`, `admin_schema()`, and a fifth document in `export_contracts`

- [ ] **Step 1: Write the failing contract test**

Append to `backend/tests/api/test_contracts.py`:

```python
def test_admin_schema_carries_every_admin_dto() -> None:
    """A DTO absent from `ADMIN_MODELS` is a DTO the frontend types by
    hand, which is the drift §7 exists to prevent. The check is by name
    against the module's own exports, so adding a model to
    `schemas/admin/` and forgetting the list fails here."""
    from triviador.api import contracts

    exported = {model.__name__ for model in contracts.ADMIN_MODELS}
    assert {"QuestionDetail", "QuestionPageView", "QuestionSaved", "CategoryView",
            "MediaAssetSummary", "ImportSummary", "ImportNotice", "InviteView",
            "IssuedInvite", "UserView", "PresetDetail", "PresetCoverage"} <= exported


def test_the_admin_document_resolves_its_refs_locally() -> None:
    from triviador.api.contracts import admin_schema

    document = json.dumps(admin_schema())
    assert "#/components/schemas/" not in document
    assert '"$ref": "#/$defs/' in document


def test_every_new_error_code_is_exported() -> None:
    from triviador.api.contracts import errors_schema

    assert {
        "media_rejected", "import_not_confirmable", "slug_taken",
        "default_preset", "last_admin", "self_target",
    } <= set(errors_schema()["api_error_code"])
```

- [ ] **Step 2: Run it and watch it fail**

Run: `cd backend && uv run pytest tests/api/test_contracts.py -q`
Expected: FAIL — `AttributeError: module 'triviador.api.contracts' has no attribute 'ADMIN_MODELS'`.

- [ ] **Step 3: Export the fifth document**

In `backend/src/triviador/api/contracts.py`, add `PresetSummary` to `REST_MODELS` (it is a player-facing response now), then:

```python
ADMIN_MODELS = (
    QuestionSummary,
    QuestionDetail,
    QuestionPageView,
    QuestionWriteRequest,
    QuestionSaved,
    CategoryView,
    CreateCategoryRequest,
    RenameCategoryRequest,
    MediaAssetSummary,
    ImportSummary,
    ImportRejection,
    ImportNotice,
    IssueInvitesRequest,
    IssuedInvite,
    InviteView,
    UserView,
    SetRoleRequest,
    PresetDetail,
    PresetWriteRequest,
    PresetCoverage,
)


def admin_schema() -> dict[str, Any]:
    """A separate document, not more `$defs` in `rest.schema.json`.

    §7's split is what keeps admin schemas out of the player bundle:
    `codegen.mjs` emits one module per document, and top-level Zod
    construction is a side effect no tree-shaker removes. A player who
    never opens `/admin` must never construct `QuestionWriteRequest`.
    """
    _, schema = models_json_schema(
        [(model, "serialization") for model in ADMIN_MODELS],
        ref_template=REF_TEMPLATE,
        title="TriviadorAdmin",
    )
    return schema
```

...and add `"admin.schema.json": admin_schema(),` to `export_contracts`'s `documents` dict.

- [ ] **Step 4: Teach the generator and the verifier about the fifth document**

**Plan 5's prediction was wrong, and this step is where it is corrected.** Its comment claims
`scripts/codegen.mjs` "generates from what `contracts/` actually contains" and will pick up a new
document "with no change to the script". It does not: the emission list is two hardcoded lines at
the bottom of the file, and the module for `rest.schema.json` is called `public.ts` — not
`rest.ts`. `scripts/verify-generated.mjs` hardcodes the same list a second time.

In `frontend/scripts/codegen.mjs`, replace the two-line tail:

```js
mkdirSync(out, { recursive: true });
emitDocument("rest.schema.json", "public.ts");
emitDocument("ws.schema.json", "ws.ts");
emitErrors();
```

...with the document table both scripts read:

```js
// One entry per exported contract document (§7). A table rather than a
// directory scan: the module name is part of the frontend's import
// surface (`@/shared/api/generated/public`), so a new contract file must
// be given a name deliberately, not have one derived from whatever the
// backend happened to call it. `verify-generated.mjs` imports this list
// so the two cannot drift — Plan 7A added `admin.ts` and found that they
// already had.
export const DOCUMENTS = [
  ["rest.schema.json", "public.ts"],
  ["ws.schema.json", "ws.ts"],
  ["admin.schema.json", "admin.ts"],
];

mkdirSync(out, { recursive: true });
for (const [document, module] of DOCUMENTS) emitDocument(document, module);
emitErrors();
```

In `frontend/scripts/verify-generated.mjs`, replace the hardcoded list with the imported one:

```js
import { DOCUMENTS } from "./codegen.mjs";

const dir = resolve(import.meta.dirname, "../src/shared/api/generated");
const modules = [...DOCUMENTS.map(([, module]) => module), "errors.ts"];
```

`codegen.mjs` runs its emission at import time, so importing `DOCUMENTS` from it re-runs
generation inside the verifier. That is harmless (it writes the same bytes `pnpm codegen` just
wrote, and `codegen:check` diffs afterwards) but it is surprising, so guard the tail of
`codegen.mjs`:

```js
if (import.meta.url === pathToFileURL(process.argv[1]).href) {
  mkdirSync(out, { recursive: true });
  for (const [document, module] of DOCUMENTS) emitDocument(document, module);
  emitErrors();
}
```

...with `import { pathToFileURL } from "node:url";` already present at the top of that file.

Then regenerate:

```bash
cd backend && uv run triviador export-contracts --out ../contracts
cd ../frontend && pnpm codegen && pnpm codegen:check && pnpm check
```

Expected: `contracts/admin.schema.json` exists, `frontend/src/shared/api/generated/admin.ts` is
emitted and loads, `codegen:check` passes (it regenerates, diffs, and evaluates all four
modules), and `steiger`/`tsc` stay green. Update `codegen.mjs`'s header comment — the paragraph
that says `admin.ts` "is absent because there are no admin DTOs yet" — to describe what the
table does now.

- [ ] **Step 5: Commit**

```bash
git add backend/src/triviador/api/contracts.py backend/tests/api/test_contracts.py \
        contracts frontend/src/shared/api/generated frontend/scripts/codegen.mjs
git commit -m "feat(contracts): export admin.schema.json and the six new error codes"
```

---

## Task 14: One admin session, end to end, against real PostgreSQL and real Garage

Plan 6 closed with `full-game.test.tsx`; this is its counterpart. Every earlier task tested one seam against fakes or one repository against the database. This one runs the whole admin story through the real app: bootstrap, bank, media, import, invite, redemption, deactivation, collection.

**Files:**
- Create: `backend/tests/api/integration/test_admin_session.py`
- Modify: `backend/tests/api/integration/conftest.py` (a Garage-backed `Settings`, an admin bootstrap helper)

**Interfaces:**
- Consumes: everything above.
- Produces: nothing new — this task adds no source file. If it needs one, a previous task is incomplete.

- [ ] **Step 1: Write the test**

Create `backend/tests/api/integration/test_admin_session.py`:

```python
"""The whole admin surface, once, in the order an operator actually uses it.

Synchronous, like every test in this directory, for the reason its
conftest gives: `TestClient` runs the app on its own loop in its own
thread. Real PostgreSQL, real Garage, real argon2 — the only thing faked
here is the wall clock's patience.
"""

import io
import zipfile

import pytest

from tests.imports.test_parse import HEADER
from tests.media.test_pipeline import png

pytestmark = pytest.mark.integration

MC_ROW = (
    "multiple_choice,Which river runs through Prague?,geography,easy,"
    "Vltava,Elbe,Morava,Ohře,0,,,river.png"
)
NUM_ROW = "numeric,In which year did the Velvet Revolution begin?,history,easy,,,,,,1989,,"


def bank_zip() -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("questions.csv", "\n".join((HEADER, MC_ROW, NUM_ROW)))
        archive.writestr("media/river.png", png(800, 400))
    return buffer.getvalue()


def test_an_admin_can_furnish_a_server_from_nothing(admin_session, media_store) -> None:
    client, settings = admin_session

    # 1. A category, because an import references one by slug.
    assert client.post(
        "/api/admin/categories", json={"slug": "geography", "name": "Geography"}
    ).status_code == 201

    # 2. A question typed by hand, with an image uploaded first.
    uploaded = client.post(
        "/api/admin/media", content=png(600, 300), headers={"Content-Type": "image/png"}
    )
    assert uploaded.status_code == 201
    asset = uploaded.json()
    # The blob is in the real bucket, re-encoded, with the immutable header.
    head = media_store.head_sync(f"{asset['id'][:2]}/{asset['id']}.webp")
    assert head.content_type == "image/webp"
    assert head.cache_control == "public, max-age=31536000, immutable"

    categories = client.get("/api/admin/categories").json()
    geography = next(c["id"] for c in categories if c["slug"] == "geography")
    created = client.post(
        "/api/admin/questions",
        json={
            "kind": "numeric",
            "prompt": "How many bridges cross the Vltava in Prague?",
            "category_id": geography,
            "difficulty": "medium",
            "media_asset_id": asset["id"],
            "choices": None,
            "numeric_answer": "18",
            "unit": None,
        },
    )
    assert created.status_code == 201

    # 3. A bulk import: dry-run refuses nothing, confirm applies it once.
    dry = client.post(
        "/api/admin/questions/import/dry-run",
        content=bank_zip(),
        headers={"Content-Type": "application/octet-stream", "X-Filename": "bank.zip"},
    )
    assert dry.status_code == 201 and dry.json()["rejected_count"] == 0
    import_id = dry.json()["import_id"]
    assert client.post(f"/api/admin/questions/import/{import_id}/confirm").status_code == 200
    assert client.post(f"/api/admin/questions/import/{import_id}/confirm").status_code == 409

    listed = client.get("/api/admin/questions?limit=100").json()
    assert listed["total"] == 3
    assert client.get("/api/admin/questions?q=vltava").json()["total"] == 2

    # 4. A preset, and its coverage readout.
    coverage = client.get("/api/admin/presets/default/coverage").json()
    assert coverage["informative"] is True
    assert coverage["required"]["numeric"] > coverage["bank"]["numeric"]  # a 3-row bank

    # 5. An invite, redeemed by a stranger, who then loses their account.
    code = client.post("/api/admin/invites", json={"count": 1}).json()[0]["code"]
    with client.__class__(client.app) as newcomer:   # a second client, no cookies
        redeemed = newcomer.post(
            "/api/auth/redeem",
            json={"code": code, "username": "newcomer", "password": "correct horse",
                  "display_name": "Newcomer"},
        )
        assert redeemed.status_code == 201
        assert newcomer.get("/api/auth/me").status_code == 200

        user_id = redeemed.json()["user_id"]
        assert client.post(f"/api/admin/users/{user_id}/deactivate").status_code == 200
        # Immediately, on the very next request, with the same cookie.
        assert newcomer.get("/api/auth/me").status_code == 401

    # 6. The admin cannot remove themselves.
    me = client.get("/api/auth/me").json()
    assert client.post(f"/api/admin/users/{me['user_id']}/deactivate").status_code == 409
    assert client.post(
        f"/api/admin/users/{me['user_id']}/role", json={"role": "player"}
    ).status_code == 409


def test_media_gc_keeps_what_a_question_still_names_and_collects_what_nothing_does(
    admin_session, media_store, run_media_gc
) -> None:
    """§10.4's two-way check, against the real store: the asset attached to
    a live question survives, and an upload nobody attached does not."""
    client, _ = admin_session
    attached = client.post(
        "/api/admin/media", content=png(120, 60), headers={"Content-Type": "image/png"}
    ).json()
    orphan = client.post(
        "/api/admin/media", content=png(121, 61), headers={"Content-Type": "image/png"}
    ).json()
    client.post("/api/admin/categories", json={"slug": "misc", "name": "Misc"})
    misc = next(
        c["id"] for c in client.get("/api/admin/categories").json() if c["slug"] == "misc"
    )
    client.post(
        "/api/admin/questions",
        json={"kind": "numeric", "prompt": "Kept?", "category_id": misc, "difficulty": "easy",
              "media_asset_id": attached["id"], "choices": None, "numeric_answer": "1",
              "unit": None},
    )

    # Dry run first: it must report the same verdict and change nothing.
    preview = run_media_gc(dry_run=True)
    assert orphan["id"] in preview.unreferenced
    assert preview.deleted is False
    assert media_store.head_sync(f"{orphan['id'][:2]}/{orphan['id']}.webp") is not None

    report = run_media_gc()
    assert orphan["id"] in report.unreferenced
    assert attached["id"] not in report.unreferenced
    assert media_store.head_sync(f"{orphan['id'][:2]}/{orphan['id']}.webp") is None
    assert media_store.head_sync(f"{attached['id'][:2]}/{attached['id']}.webp") is not None
```

- [ ] **Step 2: Add the fixtures**

Append to `backend/tests/api/integration/conftest.py`:

```python
from tests.storage.conftest import ENDPOINT, KEY_ID, KEY_SECRET
from triviador.api.app import build_dependencies, create_app
from triviador.cli import admin_create
from triviador.db.engine import engine_for, sessionmaker_for
from triviador.db.repositories.auth import UserRepository
from triviador.db.repositories.media import MediaAssetRepository
from triviador.db.security import Argon2Hasher
from triviador.media.gc import GcReport, MediaCollector
from triviador.storage.s3 import S3MediaStore


def admin_settings() -> Settings:
    """The real `Settings`, pointed at both test containers.

    Buckets are shared with `tests/storage/` on purpose: every key in
    them is either a content hash or a per-import uuid prefix, so two
    suites cannot collide, and a second pair of buckets would be a second
    thing `garage-init.sh` has to keep in step.
    """
    return Settings(
        database_url=DATABASE_URL,
        allowed_origins=("http://testserver",),
        allowed_hosts=("testserver",),
        maps_root=HERE / "maps",
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
    settings = admin_settings()
    write_grid_map(settings.maps_root / "grid")

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

    def head_sync(self, key: str) -> object | None:
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
) -> Callable[[], GcReport]:
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
                return await collector.run(now=datetime.now(UTC))

        return asyncio.run(_go())

    return run
```

Two details about the existing conftest this depends on:

- The `asyncio.run` wrapper is called `run`, not `_run`, and the reset is the `seeded` fixture
  (which depends on session-scoped `migrated`). Use `run(...)` and depend on `seeded` rather
  than adding a second pair of helpers — `seeded` also restores migration 0002's default preset
  row, which `test_an_admin_can_furnish_a_server_from_nothing` reads for its coverage check.
- **`seeded`'s TRUNCATE list has no `media_assets` and no `question_imports`** — neither table
  had rows before this plan. Add both to it, `question_imports` before `media_assets` and both
  before `questions`, or the first admin test to run leaves assets behind for the next one and
  `media-gc`'s report stops being deterministic.

- [ ] **Step 3: Run the whole suite, both containers up**

```bash
cd backend
docker compose -f docker-compose.test.yml up -d
./testing/garage-init.sh          # idempotent; safe on an already-initialised node
uv run pytest -q
uv run mypy && uv run ruff check .
cd ../frontend && pnpm check && pnpm codegen:check
```

Expected: everything passes. This is the gate for the plan as a whole.

- [ ] **Step 4: Commit**

```bash
git add backend/tests
git commit -m "test(admin): one whole admin session against real PostgreSQL and Garage"
```

---

## What this plan deliberately does not do

- **No admin UI.** Plan 7B owns `/admin/*`, shadcn/ui, the six screens, and the lobby's preset picker. This plan's only frontend artifact is a generated `admin.ts` nobody imports yet — which is exactly what makes 7B a rendering job.
- **No media browser.** Spec 1 §10.4: "There is no separate media browser in Spec 1; upload happens inside the question editor." `POST /api/admin/media` is that upload, and nothing lists assets.
- **No question hard-delete, and no reactivate route.** §7 forbids the first (event snapshots and Spec 2 analytics both read historical questions); §6.1 does not define the second, and inventing it would be a route the contract does not have.
- **No import scheduling, no background worker.** `media-gc` is a command an operator runs (§10.4), and §9.3's expiry machine is resumable precisely so it does not need a daemon. Plan 8 may put it on a timer; that is a compose concern.
- **No admin audit log.** `question_imports` keeps confirmed rows as an audit trail of imports, and that is all Spec 1 asks for. A general audit table is Spec 2.
- **No rate limiting on the admin surface.** §1.1's deployment is a LAN with two to four players and one or two admins behind an invite wall; a limiter here would be a moving part protecting nothing.
- **No Garage in the production compose.** Plan 8 owns `docker-compose.yml`, Caddy, the real buckets and the real keys. What this plan proves is that the adapters work against the pinned image, which is what open item 4 asked for.

## Self-review

**Spec coverage.** Spec 1 §10.1 → already shipped (`admin-create`, Plan 5), unchanged here. §10.2 → Tasks 4, 5 and 6 (list, filters, search, editor rules, duplicate warning). §10.3 → Tasks 7, 8 and 9 (dry-run, confirm binding to `import_id` + `upload_sha256`, zero-rejection gate, rejected CSV, `.zip`/`.csv`, media-before-transaction ordering). §10.4 → Tasks 3 and 9 (validate, re-encode, SVG refusal, immutable caching, `media-gc`'s two-way check). §10.5 → Tasks 10 and 11 (issue/list/revoke, list/deactivate/role, immediate session death with `4401`, transactional last-admin). §10.6 → Task 12 (CRUD, `validate_rules`, one default, coverage, and its explicit "informative"). Spec 1B §9.1 → Task 2 (two ports, two buckets, website-enabled media only). §9.2 → Tasks 2 and 3 (`Cache-Control` at PUT, `to_thread` behind a semaphore of one). §9.3 → Tasks 7, 8 and 9, including the post-restore rule. §6.1's admin block → Tasks 4–12, one route per line, plus the one route this plan adds and says it is adding. §6.3 → every route raises `ApiError` with a code from the closed enum. §7 → Task 13.

**Three things a reviewer should push on before execution starts.**

1. **`GET /api/presets` is a spec deviation** (Decision 1). It is small and it is load-bearing for 7B's lobby picker, but it is still a route Spec 1B does not list. Rejecting it costs Task 12 one route and 7B one screen element; accepting it later costs a contract regeneration.

2. **Task 7 validates media twice.** Dry-run re-encodes every image to decide the row is acceptable, and confirm re-encodes it again to write it. That is the price of "`rejected == 0` means confirm cannot fail", and for a 200-image import it is two CPU-bound passes behind a semaphore of one. The alternative — staging the normalized WebPs at dry-run time — writes public blobs during a phase §9.3 says writes none. If the double cost is unacceptable, the fix is to stage normalized images in the *staging* bucket and copy them across at confirm, which is a third design and should be decided now rather than in Task 8.

3. **`prompt_digest` moves to `imports/digest.py`** (Task 7, Step 3). It is a small relocation of a Plan 6 function with two existing callers (`QuestionSeeder`, `cli.parse_seed_csv`), done to keep `imports/` free of `triviador.db`. The alternative is widening the layering gate for one import, which is the kind of exception that stops a gate from meaning anything. The move is mechanical, but it does touch a module this plan otherwise leaves alone.

**What a review pass changed after the first draft.** Ten findings, all confirmed against the
code, all folded in above rather than left as notes:

1. `media-gc --dry-run` retired imports anyway — the flag reached the collector and not
   `ImportRetirer`, so the command deleted staged uploads while printing "nothing was deleted".
   `run(dry_run=...)` now reaches both halves, and `count_expirable` gives the preview its number.
2. The sweep raced the upload path in two directions: an orphan pass that could not tell a
   just-written blob from garbage, and an unreferenced asset that could gain its first reference
   between the check and the delete. Fixed by row-first deletion under `FOR UPDATE` with the
   reference check repeated inside that transaction, a grace period on the orphan pass, and
   `repair_blob` on both write paths — Decision 9, with a PostgreSQL-level test for the lock.
3. `PATCH /api/admin/presets/{id}` could clear the last default or promote a retired preset,
   both of which leave `POST /api/games` answering `no_default_preset`. `update` now returns an
   outcome and refuses both under the same lock that does the promotion.
4. Task 13 originally repeated Plan 5's claim that `codegen.mjs` would pick up a new contract
   document unchanged. It does not: the emission list is two hardcoded lines and the module for
   `rest.schema.json` is called `public.ts`, while `verify-generated.mjs` hardcodes the list a
   second time. Task 13 now edits both scripts and introduces the shared `DOCUMENTS` table.
5. Task 2's new startup assertion would have broken `tests/api/integration/`, whose `client`
   fixture calls `build_app` with no S3 credentials. That fixture is now updated in the same step.
6. Import expiry was enforced nowhere at confirm time — neither in `confirmable` nor under the
   lock — so a validated import stayed applicable forever unless `media-gc` happened to run.
   Both now check it, and `_summary` takes `now`.
7. §10.2 puts `is_active` in the editor, but the only activity route was `deactivate`, making
   retirement permanent in a bank whose rows are never deleted. `activate` is now Decision 8.
8. The parser rejected duplicate prompts inside one upload, which — because §10.3 gates confirm
   on `rejected == 0` — turned §10.2's "warning, not a block" into a block, and it never compared
   the upload against the bank at all. Both are now `Notice`s on the report, carried through the
   contract as `ImportNotice`.
9. Task 8's confirm originally passed an `ImportWriter` callback whose `write(session: object)`
   no implementation can narrow to `AsyncSession` without breaking contravariance — `mypy
   --strict` would have rejected it. The port now takes plain `ImportedQuestion`/`ImportedImage`
   data and the session never leaves `db/`.
10. The public preset route called `asdict` without importing it.
