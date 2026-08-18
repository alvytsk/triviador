# Triviador Plan 5 — API and Contracts Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Put a network in front of the runtime. A browser can redeem an invite, log in, list and create a game, join it, start it, and play it to `FINISHED` over one authenticated WebSocket — with every response and every frame drawn from a machine-checked contract that the frontend generates its types from. After this plan the backend is complete for Spec 1's player-facing surface; Plan 6 renders it, Plan 7 adds the admin half, Plan 8 deploys it.

**Architecture:** Plan 4 built a runtime that speaks domain objects and knows nothing about transports. This plan builds the transport, and the seam stays exactly where `services/ports.py` put it: `api/ws/broadcaster.py` *implements* `Broadcaster` and `GameSubscriberControl`, `api/app.py` is the composition root that constructs every concrete adapter, and no router ever reaches past a port into a session. Two things are genuinely new rather than plumbing. **Projection** turns a `GameState` into per-viewer JSON that withholds the correct answer *by not declaring the field*, and carries affordances (`your_options`) computed from `domain/maps` so the client never learns a rule. **The envelope** is total: every failure — validation, auth, a 404 Starlette raised itself, an unhandled exception — leaves through one `{code, message, details?}` shape, because a Zod boundary that meets an unparseable body has no way to report anything useful.

**Tech Stack:** Python 3.13 · `uv` · FastAPI · Starlette WebSockets · Pydantic v2 · `argon2-cffi` · `structlog` · SQLAlchemy 2.0 (async) · `asyncpg` · PostgreSQL 17 · `httpx` (ASGI transport) · `ruff` · `mypy --strict` · `pytest` + `pytest-asyncio` (`asyncio_mode = "auto"`) · pnpm + `json-schema-to-zod`

**Spec:** `docs/superpowers/specs/2026-08-16-triviador-app-architecture-design.md` §6 (REST surface, game creation, error envelope, origin checking, WebSocket hub), §7 (contracts and codegen), §10.4 (configuration and startup assertions), §10.5 (startup order), §10.6 (health), §10.10 (logging and redaction), §11 (the Layer 3 test list) · Spec 1 `docs/superpowers/specs/2026-08-07-triviador-spec1-design.md` §7 (sessions are opaque tokens), §8 (the whole realtime protocol — transport, envelope, batch transport unit, reconnect, backpressure, projection, affordances), §9.3 (first paint and the write race), §9.6 (media prefetch), §10.1 (admin bootstrap), §11.1 (four error classes and the close codes), §12.3 (the Layer 3 contract tests)

---

## Global Constraints

Every task's requirements implicitly include this section.

- **The domain stays pure.** `domain/` must not import `db/`, `services/`, `runtime/`, `api/`, `sqlalchemy`, `asyncpg`, `alembic`, `pydantic`, or `fastapi`. `tests/test_layering.py` proves it. Task 2 amends the domain and must leave that gate green.
- **`services/` declares Protocols, enums and frozen value types, and nothing else.** No implementation, no `db`, `runtime`, `api`, `sqlalchemy` or `fastapi` import. Spec 1B §5.1: "no implementation lives under `services/`."
- **`runtime/` never imports `db/` or `api/`.** Unchanged by this plan, and still enforced.
- **`api/` is the only layer allowed to import everything.** It is the composition root (§5.1). That privilege is exactly why the *other* gates matter, and why `api/projection/` is separately gated: it may import `domain` and `pydantic`, never `db`, `sqlalchemy` or `fastapi` — projection is a pure function of state and viewer, and a projection module that can open a session is a projection module that eventually will.
- **`DomainEvent` and `ServerMessage` share no base class** (§8.7). `websocket.send_json(event.model_dump())` must not typecheck, and `project()` returning a domain event must be a `mypy --strict` error rather than a leak discovered in a browser.
- **The pre-resolution DTO does not declare the answer fields at all** (§8.7, §12.3). Withholding by `exclude=` or by `None` is not acceptable: the guarantee is structural, so no future `model_dump` flag can undo it.
- **Every client-frame model is `ConfigDict(extra="forbid")`** and every generated Zod object is `.strict()` (§6.5). Omitting `actor_id` from a schema is not enough if extra keys are ignored.
- **Client frames carry no actor.** Identity comes from the authenticated principal, in the one layer that enforces it (§6.5, §11). A frame carrying `actor_id` is rejected by strictness before an actor could be derived from it; both properties are asserted separately.
- **Every response body is an envelope or a declared success model.** No route, no handler, and no Starlette default may emit anything else (§6.3).
- **A 500 body is sanitized**: a stable code, a generic message, and the request id. Never an exception message, never a traceback (§6.3).
- **Logs must never contain** answers or answer values, command payloads, passwords, cookies, session tokens, invite codes, or S3 credentials (§10.10). The guarantee is about *which fields are emitted*, not about scanning bytes.
- **The runtime is never awaited from a socket write path, and a socket write is never awaited from the runtime.** `publish` is synchronous and only `put_nowait`s (§8.6).
- **No test waits on wall-clock time** for game logic. `Clock.sleep_until` takes an absolute instant and the fake clock is driven by explicit `advance_to` (Spec 1 §12.2). The one exception this plan inherits is `tests/runtime/integration/conftest.py`'s 1 ms poll, which lets a real in-flight asyncpg round trip land; it is not simulated game time.
- **`NewType` aliases are constructed, never implied.** `PlayerId`, `GameId`, `RegionId`, `MapId`, `DeadlineId`, and this plan's `UserId` / `SessionId` are `NewType`s (`domain/ids.py`). Every literal goes through its constructor.
- **Every timestamp is timezone-aware UTC**, on the wire as ISO-8601 with an explicit offset.
- Python `>=3.13`. Line length 100. `ruff check`, `ruff format --check`, and `mypy --strict` must pass on every commit.
- **`reducer.py` and `db/codec/` keep their 100 % branch coverage gates.** Task 2 modifies `reducer.py` and must leave it at 100 %.
- **Integration tests run against real PostgreSQL, never SQLite,** carry `pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="session")]`, and fail loudly rather than skipping when the database is absent.
- **Contract tests carry no database.** Layer 3 runs the real ASGI app over `httpx.ASGITransport` with fake stores, a fake hasher, and a fake clock. A contract suite that needs PostgreSQL running is a contract suite people stop running.

---

## File Structure

```
backend/
├── pyproject.toml                    MODIFY  fastapi, argon2-cffi, structlog, httpx; CLI entry point
├── .env.example                      CREATE  §10.4's variable list, with placeholders
└── src/triviador/
    ├── config.py                     MODIFY  API settings + the two §10.4 startup assertions
    ├── cli.py                        CREATE  export-contracts, admin-create
    ├── domain/
    │   ├── ids.py                    MODIFY  UserId, SessionId
    │   ├── game/actions.py           MODIFY  SubmitAnswer loses `elapsed_ms` (Task 2)
    │   └── game/reducer.py           MODIFY  the server measures the answer clock
    ├── services/
    │   ├── ports.py                  MODIFY  GameCatalogPort, PresetPort
    │   └── identity.py               CREATE  UserRole, AuthenticatedPrincipal, the auth Protocols
    ├── db/
    │   ├── security.py               CREATE  Argon2Hasher, token hashing
    │   ├── repositories/auth.py      CREATE  UserRepository, SessionRepository, InviteRepository
    │   ├── repositories/presets.py   CREATE  PresetRepository (read-only; CRUD is Plan 7)
    │   ├── seed.py                   CREATE  frozen literals the migrations wrote
    │   └── migrations/versions/
    │       └── 0002_default_preset.py CREATE  §7's "never zero default presets"
    └── api/
        ├── __init__.py               CREATE
        ├── app.py                    CREATE  composition root, lifespan, router mounting
        ├── deps.py                   CREATE  principal, manager, stores — from app.state
        ├── errors.py                 CREATE  ApiErrorCode, the envelope, the total handlers
        ├── logging.py                CREATE  structlog config, request id, redaction
        ├── middleware.py             CREATE  origin checking, trusted hosts, body size
        ├── contracts.py              CREATE  the four exported JSON documents
        ├── schemas/
        │   ├── __init__.py           CREATE
        │   ├── errors.py             CREATE  ErrorEnvelope
        │   ├── auth.py               CREATE  redeem/login/me DTOs
        │   ├── maps.py               CREATE  MapSummary, MapDetail — never adjacency
        │   ├── games.py              CREATE  GameSummary, GameSnapshot, ClientGameState, turns
        │   └── ws.py                 CREATE  ClientMessage | ServerMessage, both strict
        ├── projection/               ← PURE: domain + pydantic only. Gated.
        │   ├── __init__.py           CREATE
        │   ├── viewer.py             CREATE  ViewerContext, viewer_for
        │   ├── snapshot.py           CREATE  project_snapshot
        │   ├── turns.py              CREATE  the turn DTO + your_options
        │   └── events.py             CREATE  project(event, viewer) -> ClientEvent | None
        ├── http/
        │   ├── __init__.py           CREATE
        │   ├── auth.py               CREATE  redeem, login, logout, me
        │   ├── maps.py               CREATE  list, detail
        │   ├── games.py              CREATE  list, create, get, join, start
        │   └── health.py             CREATE  live, ready
        └── ws/
            ├── __init__.py           CREATE
            ├── hub.py                CREATE  Connection, Hub, topics, sender task
            ├── origins.py            CREATE  WsOrigin — resolves into the outbound queue
            ├── broadcaster.py        CREATE  the two ports, per-viewer projection
            └── endpoint.py           CREATE  handshake, hello/ping, frame dispatch

backend/tests/
├── test_layering.py                  MODIFY  api/projection/ gate; services/ still closed
├── domain/game/test_answer_clock.py  CREATE  Task 2
├── api/                              ← CONTRACT: no database, no argon2, no wall clock.
│   ├── __init__.py · conftest.py     CREATE  the ASGI app over fakes
│   ├── fakes.py                      CREATE  FakeUserStore, FakeSessionStore, …
│   ├── test_envelope.py              CREATE
│   ├── test_logging.py               CREATE  request id + redaction
│   ├── test_origin.py                CREATE
│   ├── test_auth.py                  CREATE
│   ├── test_maps.py                  CREATE
│   ├── test_games.py                 CREATE
│   ├── test_health.py                CREATE
│   ├── test_projection_snapshot.py   CREATE
│   ├── test_projection_turns.py      CREATE
│   ├── test_projection_events.py     CREATE
│   ├── test_ws_schemas.py            CREATE
│   ├── test_ws_hub.py                CREATE
│   ├── test_ws_endpoint.py           CREATE
│   ├── test_broadcaster.py           CREATE
│   └── test_contracts.py             CREATE
├── db/test_auth_repositories.py      CREATE  INTEGRATION
├── db/test_presets.py                CREATE  INTEGRATION
└── api/integration/                  ← INTEGRATION: real PostgreSQL behind the real app.
    ├── __init__.py · conftest.py     CREATE
    └── test_play_through_http.py     CREATE  create → join → start → FINISHED over HTTP + WS

contracts/                            CREATE  exported, committed, drift-checked
├── openapi.json · rest.schema.json · ws.schema.json · errors.json

frontend/                             CREATE  contracts consumer only — Plan 6 adds the app
├── package.json · pnpm-lock.yaml · .gitignore
├── scripts/codegen.mjs
└── shared/api/generated/{public,ws,errors}.ts
```

**Why `api/projection/` is its own gated package.** It is the only part of this layer that is a pure function, it is where the answer-withholding guarantee lives, and it is the piece Plan 6's tests will want to reason about. Keeping it importable without FastAPI is what lets `test_projection_*.py` construct a `GameState` from `tests/conftest.py`'s builders and assert on a dict — no app, no client, no event loop.

---

## Design decisions this plan makes that the spec does not state

Grounding the spec against the code Plans 2–4 produced surfaced one defect and eleven unstated choices. Each is resolved here, in the open.

1. **The answer clock is the server's, and `SubmitAnswer.elapsed_ms` must go.** `_record_answer` copies `command.elapsed_ms` straight into `SubmittedAnswer`, and `_rank_numeric`'s sort key is `(wrong?, |error|, elapsed_ms, seat)`. Nothing validates it. A client that always reports `elapsed_ms: 0` therefore wins every numeric tie it is part of — every expansion ranking, every battle tiebreak, and the final tiebreak that can decide the match. This is invisible today only because there is no client. Plan 5 is the layer that first accepts client input, so Plan 5 closes it: Task 2 removes the field from the command and has `_record_answer` derive it from `ctx.now` and the open window (`answer_timeout_ms - (deadline_at - now)`, clamped). Replay is unaffected — the derived value is persisted inside `AnswerSubmitted`, which the codec already stores, so no migration and no golden-corpus change. The cost is that server-measured elapsed includes the round trip; on §1.1's LAN that is single-digit milliseconds, and §8.3 already makes the server authoritative on time ("the server's `ctx.now >= deadline_at` stays authoritative"). Trading a bounded latency penalty for an unbounded, unauthenticated cheat is not a close call.

2. **The envelope's `code` is one closed union of two disjoint enums.** §6.3 wants one envelope whose `code` is closed; §6.3 also wants `RejectedCommand → 409 + its RejectCode`; §7 exports `ApiErrorCode + RejectCode` in `errors.json`. Rather than burying the reject code inside `details` — which would force every client to switch twice — `code` is typed `ApiErrorCode | RejectCode`, both halves exported, and a test asserts the two value sets are **disjoint**. Without that test the union silently stops being a discriminator the first time someone adds `not_found` to both.

3. **REST's view of the games table is a new port, not a widening of `GameQueriesPort`.** `ports.py` invites Plan 5 to widen it. Widening it would force every runtime fake — `tests/runtime/fakes.py` and three integration fixtures — to grow `create`, `get_summary` and `list_joinable` methods the runtime never calls. `GameCatalogPort` is declared alongside instead, `GameRepository` satisfies both, and neither consumer sees the other's methods.

4. **Auth capabilities enter through Protocols too.** `UserStore`, `SessionStore`, `InviteStore`, `PasswordHasher` and `PresetPort` are declared in `services/identity.py` and implemented in `db/`. This is not symmetry for its own sake: argon2 is *deliberately* ~50 ms per hash, and a Layer 3 suite that pays that per login — plus a PostgreSQL round trip — is a suite that gets marked slow and then gets skipped. Fakes make the whole contract suite run in milliseconds; the real adapters get their own integration tests.

5. **`SessionStore.resolve` returns a principal, not a row.** Expiry, revocation, and `users.is_active` are three ways one session is dead, and the spec requires deactivation to log a user out *now* (Spec 1 §7). Putting all three in one query behind one method means there is exactly one place that can get it wrong, and `test_auth_repositories.py` asserts each of the three independently.

6. **A user's `PlayerId` is their `UserId`.** `games.host_id` and `game_players.user_id` are both FKs to `users.id`, and `JoinGame.actor_id` is a `PlayerId`. So `UserId` and `SessionId` are added to `domain/ids.py` for the identity layer's own clarity, and `ViewerContext.player_id` is `PlayerId(user_id)` **iff** that id is in `state.players`, `None` otherwise. It is a membership test, never a lookup table.

7. **The default rule preset is seeded by a migration.** `POST /api/games` takes `{preset_id, map_id}`, and §7 makes the database enforce "at most one default" while leaving "never zero" to application logic. A migration inserting `DEFAULT_RULES` as `id='default'` is that logic, applied once, at the only moment the system is guaranteed to be quiescent. `preset_id` is therefore optional on the request: absent means the default, and a database with no default answers 409 `NO_DEFAULT_PRESET` rather than 500.

8. **The client frame union is flat.** §6.5 enumerates "surrender, subscribe, unsubscribe, or ping" in one breath when saying where `deadline_id` may not appear, which only parses if transport frames and commands share one `type` discriminator. So `ClientMessage` is a single discriminated union over `subscribe · unsubscribe · resync · ping · submit_answer · pick_region · select_attack_target · surrender`, and each command variant nests its own strict `payload` per §6.5's `{command_id, game_id, deadline_id?, type, payload}`.

9. **`admin.ts` is not generated by this plan.** §7 lists four generated modules; admin DTOs do not exist until Plan 7. `scripts/codegen.mjs` generates from what `contracts/` actually contains, so Plan 7 adds `contracts/admin.schema.json` and gets `admin.ts` with no change to the script. Generating an empty `admin.ts` now would commit a file whose only content is a promise.

10. **`admin-create` ships here.** Spec 1 §10.1 is admin bootstrap, and admin is Plan 7 — but the CLI module exists in this plan anyway for `export-contracts`, invites can only be created by an admin, and a login endpoint reachable by nobody cannot be exercised end to end. It is forty lines with three stated semantics, and Plan 7 builds invites on top of it.

11. **Media URLs are emitted; media bytes are not served.** §9.6 requires `game.snapshot.media_prefetch: string[]`. This plan builds those URLs as `{MEDIA_PUBLIC_BASE}/{media_asset_id}` and asserts they are opaque and content-addressed. The route that returns the bytes is Garage's, and belongs to Plans 7/8.

12. **A WebSocket command's origin never holds a future.** §8.2 is explicit that the WS handler must not await one. `WsOrigin` implements the `Origin` protocol by `put_nowait`ing an `error` frame — carrying the `command_id` for correlation (§8.3) — onto the connection's own outbound queue, and by doing nothing at all on success, because success is published to every subscriber by the broadcaster. Like every origin it is non-throwing and idempotent: a queue that is full at that moment closes the connection with `4408`, it does not raise into the consumer loop.

---

## Task 1: The API package, its settings, and the two gates that keep the layering honest

**Files:**
- Modify: `backend/pyproject.toml`
- Create: `backend/.env.example`
- Modify: `backend/src/triviador/config.py`
- Create: `backend/src/triviador/api/__init__.py`, `backend/src/triviador/api/projection/__init__.py`
- Modify: `backend/tests/test_layering.py`
- Test: `backend/tests/api/__init__.py`, `backend/tests/api/test_settings.py`

**Interfaces:**
- Consumes: `triviador.config.Settings` (Plan 4's runtime tunables), `tests/test_layering.py`'s `_imported_modules` / `_is_forbidden` helpers.
- Produces: `Settings.allowed_origins`, `.allowed_hosts`, `.cookie_secure`, `.session_ttl_days`, `.maps_root`, `.media_public_base`, `.log_level`, `.log_format`, `.max_body_bytes`, `.ws_outbound_queue_size`, `.session_cookie_name`; `config.startup_problems(settings) -> tuple[str, ...]`; `config.PLACEHOLDER = "CHANGE_ME"`.

- [ ] **Step 1: Write the failing tests**

`backend/tests/api/__init__.py` is empty. `backend/tests/api/test_settings.py`:

```python
"""§10.4's two startup assertions, and the comma-separated list form.

These are *startup* assertions deliberately: a misconfigured origin list
fails authentication in a way that looks like a frontend bug, hours later
and on someone else's machine.
"""

from pathlib import Path

import pytest

from triviador.config import PLACEHOLDER, Settings, startup_problems


def settings(**overrides: object) -> Settings:
    base: dict[str, object] = {
        "database_url": "postgresql+asyncpg://u:p@localhost/db",
        "allowed_origins": ("http://box.lan",),
        "cookie_secure": False,
        "maps_root": Path("/data/maps"),
    }
    return Settings(**{**base, **overrides})  # type: ignore[arg-type]


def test_a_comma_separated_origin_list_parses_into_a_tuple() -> None:
    assert settings(allowed_origins="http://a.lan, http://b.lan").allowed_origins == (
        "http://a.lan",
        "http://b.lan",
    )


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("http://localhost:5173", ("http://localhost:5173",)),
        ("http://a.lan, http://b.lan", ("http://a.lan", "http://b.lan")),
    ],
    ids=["single-no-comma", "pair-with-whitespace"],
)
def test_origins_parse_from_a_real_environment_variable(
    monkeypatch: pytest.MonkeyPatch, raw: str, expected: tuple[str, ...]
) -> None:
    """Through `EnvSettingsSource`, not through kwargs.

    `Settings(**overrides)` bypasses environment sourcing entirely, so a
    suite that only ever does that cannot see the failure that actually
    ships: pydantic-settings JSON-decodes a complex field's raw string
    before any `mode="before"` validator runs, and a bare URL is not JSON.
    The single-value case is the one that matters most — it is the exact
    value committed in `.env.example`, and it has no comma, so nothing
    about it looks like a list.
    """
    monkeypatch.setenv("TRIVIADOR_DATABASE_URL", "postgresql+asyncpg://u:p@localhost/db")
    monkeypatch.setenv("TRIVIADOR_ALLOWED_ORIGINS", raw)
    assert Settings().allowed_origins == expected  # type: ignore[call-arg]


def test_hosts_parse_from_a_real_environment_variable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`allowed_hosts` carries the same annotation and therefore the same
    hazard; asserting only on origins would leave half the fix unproven."""
    monkeypatch.setenv("TRIVIADOR_DATABASE_URL", "postgresql+asyncpg://u:p@localhost/db")
    monkeypatch.setenv("TRIVIADOR_ALLOWED_HOSTS", "localhost, 127.0.0.1")
    assert Settings().allowed_hosts == ("localhost", "127.0.0.1")  # type: ignore[call-arg]


def test_a_consistent_configuration_has_no_problems() -> None:
    assert startup_problems(settings()) == ()


def test_an_https_origin_with_an_insecure_cookie_is_refused() -> None:
    problems = startup_problems(settings(allowed_origins=("https://box.lan",), cookie_secure=False))
    assert any("COOKIE_SECURE" in p for p in problems)


def test_an_http_origin_with_a_secure_cookie_is_refused() -> None:
    """The failure this catches is silent: a `Secure` cookie is simply never
    sent over plain HTTP, so every request arrives unauthenticated and the
    only symptom is a login that appears to succeed and then does nothing."""
    problems = startup_problems(settings(allowed_origins=("http://box.lan",), cookie_secure=True))
    assert any("COOKIE_SECURE" in p for p in problems)


def test_a_mixed_scheme_origin_list_is_refused_under_either_cookie_setting() -> None:
    mixed = ("http://box.lan", "https://box.lan")
    assert startup_problems(settings(allowed_origins=mixed, cookie_secure=False)) != ()
    assert startup_problems(settings(allowed_origins=mixed, cookie_secure=True)) != ()


def test_a_setting_still_holding_its_placeholder_is_refused() -> None:
    url = f"postgresql+asyncpg://u:{PLACEHOLDER}@localhost/db"
    problems = startup_problems(settings(database_url=url))
    assert any("database_url" in p for p in problems)


def test_an_empty_origin_list_is_refused() -> None:
    """Not a vacuous truth: with no origins every unsafe request is refused
    and every socket handshake fails, which reads as a broken deploy rather
    than as a missing variable."""
    assert startup_problems(settings(allowed_origins=())) != ()


@pytest.mark.parametrize("origin", ["box.lan", "http://box.lan/", "http://box.lan/app"])
def test_an_origin_that_is_not_a_bare_scheme_and_host_is_refused(origin: str) -> None:
    """A browser sends `Origin: scheme://host[:port]` with no path and no
    trailing slash. An entry with either can never match, so the mismatch
    must surface at startup rather than as a 403 nobody can explain."""
    assert startup_problems(settings(allowed_origins=(origin,))) != ()
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd backend && uv run pytest tests/api/test_settings.py -v --no-cov`
Expected: FAIL — `ImportError: cannot import name 'PLACEHOLDER' from 'triviador.config'`

- [ ] **Step 3: Add the dependencies and the CLI entry point**

In `backend/pyproject.toml`, extend `[project] dependencies` and `[dependency-groups] dev`, and add the script:

```toml
dependencies = [
    "sqlalchemy[asyncio]>=2.0.36",
    "asyncpg>=0.30",
    "alembic>=1.14",
    "pydantic>=2.10",
    "pydantic-settings>=2.6",
    "fastapi>=0.115",
    "uvicorn[standard]>=0.32",
    "argon2-cffi>=23.1",
    "structlog>=24.4",
]

[dependency-groups]
dev = [
    "pytest>=8.3",
    "pytest-asyncio>=0.24",
    "pytest-cov>=6.0",
    "hypothesis>=6.115",
    "mypy>=1.13",
    "ruff>=0.8",
    "httpx>=0.28",
]

[project.scripts]
triviador = "triviador.cli:main"
```

Then `uv sync`. `triviador.cli` does not exist until Task 18; an entry point naming a missing module is inert until someone runs it, and declaring it now keeps the packaging change in the task that makes it.

- [ ] **Step 4: Write `.env.example`**

`backend/.env.example` — every §10.4 variable, with `CHANGE_ME` wherever a secret belongs. The S3 and Garage entries are Plan 8's; they are listed now because §10.4's assertion is "no secret still holds its placeholder", and a variable that appears only in Plan 8 is a variable nobody adds to the example file.

```sh
# Copy to .env (mode 0600) and replace every CHANGE_ME.
TRIVIADOR_DATABASE_URL=postgresql+asyncpg://triviador:CHANGE_ME@postgres:5432/triviador
TRIVIADOR_ALLOWED_ORIGINS=http://localhost:5173
TRIVIADOR_ALLOWED_HOSTS=localhost,127.0.0.1
TRIVIADOR_COOKIE_SECURE=false
TRIVIADOR_SESSION_TTL_DAYS=30
TRIVIADOR_MAPS_ROOT=/data/maps
TRIVIADOR_MEDIA_PUBLIC_BASE=/media
TRIVIADOR_LOG_LEVEL=INFO
TRIVIADOR_LOG_FORMAT=json
# Plan 8 (compose, Garage) reads these; they are listed here so the
# placeholder assertion covers them from the first deploy.
POSTGRES_PASSWORD=CHANGE_ME
GARAGE_RPC_SECRET=CHANGE_ME
```

- [ ] **Step 5: Extend `Settings` and add `startup_problems`**

Append to `backend/src/triviador/config.py` (inside `Settings`, then at module level):

```python
PLACEHOLDER = "CHANGE_ME"

# A browser's Origin header is `scheme://host[:port]` — no path, no trailing
# slash. Matching is exact string equality against this list, so an entry in
# any other shape is dead weight that can only ever produce a 403.
_ORIGIN_RE = re.compile(r"^https?://[A-Za-z0-9.\-]+(:\d+)?$")


class Settings(BaseSettings):
    ...  # existing fields unchanged

    # --- API (Spec 1B §6, §10.4) ------------------------------------------
    # `NoDecode` is load-bearing, not decoration. Without it,
    # `EnvSettingsSource` runs `json.loads()` on the raw environment string
    # *before* any `mode="before"` validator sees it, and for a plain
    # (non-Union) complex type it does not swallow the failure — so
    # `TRIVIADOR_ALLOWED_ORIGINS=http://box.lan` raises `SettingsError` at
    # startup even with no comma in it, because a bare URL is not JSON.
    # `NoDecode` tells the source to hand the string over untouched, which
    # is what makes `_split_csv` below reachable at all.
    allowed_origins: Annotated[tuple[str, ...], NoDecode] = ()
    allowed_hosts: Annotated[tuple[str, ...], NoDecode] = ("*",)
    cookie_secure: bool = False
    session_cookie_name: str = "triviador_session"
    session_ttl_days: int = 30
    maps_root: Path = Path("/data/maps")
    media_public_base: str = "/media"
    log_level: str = "INFO"
    log_format: Literal["json", "console"] = "json"
    # 413 above this. Spec 1's largest player-facing body is a login form;
    # Plan 7's media upload sets its own, larger limit on its own route.
    max_body_bytes: int = 1_048_576
    # §8.6's bounded outbound queue. Overflow closes that subscriber (4408).
    ws_outbound_queue_size: int = 64
    # §8.6: "ping every 15 s, socket considered dead after 30 s of silence."
    # Both ends apply it. Without the server half, a half-open TCP
    # connection — a laptop lid closing, a Wi-Fi handover — leaves a
    # `Connection`, a sender task and a presence entry behind forever.
    ws_idle_timeout_s: float = 30.0

    @field_validator("allowed_origins", "allowed_hosts", mode="before")
    @classmethod
    def _split_csv(cls, value: object) -> object:
        """`TRIVIADOR_ALLOWED_ORIGINS=http://a,http://b`.

        pydantic-settings parses a complex annotation from JSON by default,
        which would make the natural env-file form a startup crash with a
        JSON decode error pointing at nothing useful. Reachable only
        because both fields are annotated `NoDecode` — see above.
        """
        if isinstance(value, str):
            return tuple(part.strip() for part in value.split(",") if part.strip())
        return value


def startup_problems(settings: Settings) -> tuple[str, ...]:
    """§10.4's two assertions, as a list rather than a raise.

    A list, so a misconfigured deploy is told about *every* problem at once
    instead of one per restart.
    """
    problems: list[str] = []

    if not settings.allowed_origins:
        problems.append("ALLOWED_ORIGINS is empty: no request and no socket could be accepted")
    malformed = [o for o in settings.allowed_origins if not _ORIGIN_RE.match(o)]
    if malformed:
        problems.append(f"ALLOWED_ORIGINS entries must be scheme://host[:port]: {malformed}")

    wanted = "https" if settings.cookie_secure else "http"
    wrong = [o for o in settings.allowed_origins if not o.startswith(f"{wanted}://")]
    if wrong:
        problems.append(
            f"COOKIE_SECURE={settings.cookie_secure} requires every ALLOWED_ORIGINS entry "
            f"to use {wanted}://; these do not: {wrong}"
        )

    for name, value in settings.model_dump().items():
        if isinstance(value, str) and PLACEHOLDER in value:
            problems.append(f"{name} still holds its .env.example placeholder")

    return tuple(problems)
```

Add `import re`, `from pathlib import Path`, `from typing import Annotated, Literal`, `from pydantic import field_validator`, and `NoDecode` to the existing `pydantic_settings` import.

`startup_problems` scans `settings.model_dump()`, so it can only see values `Settings` actually declares. `POSTGRES_PASSWORD` and `GARAGE_RPC_SECRET` appear in `.env.example` but are read by compose and by Garage, not by this process — they are covered when Plan 8 adds the S3 settings block, and the scan then covers them for free because it iterates every field rather than a hand-written list. That is the reason it iterates rather than naming `database_url`.

Note that the mixed-scheme case needs no rule of its own: whichever scheme `COOKIE_SECURE` does not want is reported by the check above, which is why the mixed-list test asserts a non-empty result under *both* settings rather than a specific message.

- [ ] **Step 6: Run the settings tests**

Run: `cd backend && uv run pytest tests/api/test_settings.py -v --no-cov`
Expected: PASS (9 tests)

- [ ] **Step 7: Create the empty API packages and extend the layering gate**

`backend/src/triviador/api/__init__.py` and `backend/src/triviador/api/projection/__init__.py`, both empty.

In `backend/tests/test_layering.py`: add `"starlette"` to `FORBIDDEN` (the domain gate) — `fastapi` is already there, and Starlette is what a lazy `from starlette.responses import ...` would reach for. Add `"fastapi"` and `"starlette"` to the forbidden tuple inside `test_services_does_not_import_adapters`. Then add the new gate:

```python
PROJECTION = SRC / "triviador" / "api" / "projection"


def test_projection_stays_a_pure_function_of_state_and_viewer() -> None:
    """`api/` as a whole is the composition root and may import anything.
    `api/projection/` may not: it is where §8.7's per-viewer withholding
    lives, and it is called from inside the synchronous broadcaster, on the
    consumer task's own stack (§8.6). A projection module that can open a
    session is a projection module that will eventually await one there —
    which is the one thing `Broadcaster` being a `def` exists to prevent.

    Empty today (the package lands in Tasks 8-10); `rglob` over a directory
    with only `__init__.py` yields no violations, and the gate is written
    now so the first projection module is already covered.
    """
    forbidden = (
        "triviador.db",
        "triviador.runtime",
        "triviador.api.http",
        "triviador.api.ws",
        "sqlalchemy",
        "asyncpg",
        "alembic",
        "fastapi",
        "starlette",
    )
    violations = [
        f"{path.relative_to(SRC)}: {module}"
        for path in sorted(PROJECTION.rglob("*.py"))
        for module in sorted(_imported_modules(path))
        if _is_forbidden(module, forbidden)
    ]
    assert violations == [], violations
```

- [ ] **Step 8: Run the full suite**

Run: `cd backend && uv run pytest -q && uv run ruff check && uv run ruff format --check && uv run mypy --strict`
Expected: all green — the new gate passes vacuously, nothing else moved.

- [ ] **Step 9: Commit**

```bash
git add backend/pyproject.toml backend/uv.lock backend/.env.example \
        backend/src/triviador/config.py backend/src/triviador/api \
        backend/tests/test_layering.py backend/tests/api
git commit -m "feat(api): API settings, the two startup assertions, and the projection gate"
```

---

## Task 2: The answer clock belongs to the server

The defect from design decision 1. This is a domain change, so it comes before anything is built on the wrong command shape.

**Files:**
- Modify: `backend/src/triviador/domain/game/actions.py`
- Modify: `backend/src/triviador/domain/game/reducer.py:243`
- Modify: every test constructing `SubmitAnswer` (`tests/domain/game/`, `tests/runtime/`, `tests/db/`)
- Test: `backend/tests/domain/game/test_answer_clock.py`

**Interfaces:**
- Consumes: `SubmitAnswer(actor_id, deadline_id, value)`, `DecisionContext.now`, `Deadline.deadline_at`, `GameRules.answer_timeout_ms`.
- Produces: `SubmitAnswer` **without** `elapsed_ms`; `reducer.elapsed_ms_for(turn, rules, now) -> int`.

- [ ] **Step 1: Write the failing test**

`backend/tests/domain/game/test_answer_clock.py`:

```python
"""§8.3: the server is authoritative on time, including how fast an answer was.

`_rank_numeric` breaks ties on `elapsed_ms`. While that number came from the
command, a client reporting 0 won every tie it entered — every expansion
ranking, every battle tiebreak, and the final tiebreak that can decide the
match. The command no longer carries it.
"""

from dataclasses import replace
from datetime import timedelta
from decimal import Decimal

import pytest

from tests.conftest import expire_warmup, full_pool, lobby_state
from triviador.domain.game.actions import (
    DecisionContext,
    StartGame,
    SubmitAnswer,
)
from triviador.domain.game.events import AnswerSubmitted
from triviador.domain.game.reducer import decide, fold
from triviador.domain.game.rules import DEFAULT_RULES
from triviador.domain.game.state import ExpansionQuestion, NumericAnswer
from triviador.domain.ids import PlayerId, RegionId


def question_open() -> "tuple[object, object]":
    """A state with an ExpansionQuestion window open, and the instant it opened."""
    state = lobby_state()
    started = fold(
        state,
        decide(
            state,
            StartGame(PlayerId("p1")),
            DecisionContext(
                now=NOW,
                shuffled_player_ids=(PlayerId("p1"), PlayerId("p2"), PlayerId("p3")),
                base_regions=(RegionId("r0"), RegionId("r2"), RegionId("r6")),
                drawn_pool=full_pool(),
            ),
        ),
    )
    state = expire_warmup(started)
    assert isinstance(state.turn, ExpansionQuestion)
    opened_at = state.turn.deadline.deadline_at - timedelta(
        milliseconds=state.rules.answer_timeout_ms
    )
    return state, opened_at


def test_submit_answer_no_longer_carries_an_elapsed_time() -> None:
    with pytest.raises(TypeError):
        SubmitAnswer(  # type: ignore[call-arg]
            actor_id=PlayerId("p1"),
            deadline_id=DEADLINE,
            value=NumericAnswer(Decimal(1)),
            elapsed_ms=0,
        )


def test_the_recorded_elapsed_time_is_measured_from_the_window_opening() -> None:
    state, opened_at = question_open()
    events = decide(
        state,
        SubmitAnswer(PlayerId("p1"), state.turn.deadline.id, NumericAnswer(Decimal(7))),
        DecisionContext(now=opened_at + timedelta(milliseconds=1234)),
    )
    submitted = next(e for e in events if isinstance(e, AnswerSubmitted))
    assert submitted.answer.elapsed_ms == 1234


def test_an_answer_at_the_very_start_of_the_window_records_zero() -> None:
    state, opened_at = question_open()
    events = decide(
        state,
        SubmitAnswer(PlayerId("p1"), state.turn.deadline.id, NumericAnswer(Decimal(7))),
        DecisionContext(now=opened_at),
    )
    submitted = next(e for e in events if isinstance(e, AnswerSubmitted))
    assert submitted.answer.elapsed_ms == 0


def test_a_clock_that_appears_to_run_backwards_still_records_a_sane_elapsed() -> None:
    """`ctx.now` before the window opened cannot happen from a wall clock,
    but it can from a recovered deadline whose `deadline_at` was written by
    a differently-skewed process. A negative elapsed would sort *ahead* of
    every honest answer — the exact cheat this task removes — so it clamps."""
    state, opened_at = question_open()
    events = decide(
        state,
        SubmitAnswer(PlayerId("p1"), state.turn.deadline.id, NumericAnswer(Decimal(7))),
        DecisionContext(now=opened_at - timedelta(seconds=5)),
    )
    submitted = next(e for e in events if isinstance(e, AnswerSubmitted))
    assert submitted.answer.elapsed_ms == 0


def test_an_answer_landing_after_the_deadline_clamps_to_the_full_window() -> None:
    """The window is still open — nothing has expired it yet — so the answer
    counts, but it can never be recorded as slower than the window was long,
    or the tiebreak key stops being comparable across windows."""
    state, opened_at = question_open()
    events = decide(
        state,
        SubmitAnswer(PlayerId("p1"), state.turn.deadline.id, NumericAnswer(Decimal(7))),
        DecisionContext(now=opened_at + timedelta(seconds=999)),
    )
    submitted = next(e for e in events if isinstance(e, AnswerSubmitted))
    assert submitted.answer.elapsed_ms == DEFAULT_RULES.answer_timeout_ms


def test_the_faster_of_two_equally_wrong_answers_still_wins() -> None:
    """The property `_rank_numeric` actually depends on, now that neither
    player can assert their own speed."""
    state, opened_at = question_open()
    at = lambda ms: DecisionContext(now=opened_at + timedelta(milliseconds=ms))  # noqa: E731
    state = fold(
        state, decide(state, SubmitAnswer(PlayerId("p2"), state.turn.deadline.id,
                                          NumericAnswer(Decimal(10))), at(3000))
    )
    state = fold(
        state, decide(state, SubmitAnswer(PlayerId("p1"), state.turn.deadline.id,
                                          NumericAnswer(Decimal(10))), at(500))
    )
    assert isinstance(state.turn, ExpansionQuestion)
    answers = state.turn.answers
    assert answers[PlayerId("p1")].elapsed_ms < answers[PlayerId("p2")].elapsed_ms
```

Bind `NOW` and `DEADLINE` at the top: `from tests.conftest import NOW` and `DEADLINE = DeadlineId(1)`.

- [ ] **Step 2: Run to verify it fails**

Run: `cd backend && uv run pytest tests/domain/game/test_answer_clock.py -v --no-cov`
Expected: FAIL — `SubmitAnswer` still accepts `elapsed_ms`, so `test_submit_answer_no_longer_carries_an_elapsed_time` fails on the missing `TypeError` and the rest fail on the missing argument.

- [ ] **Step 3: Remove the field and derive the value**

In `actions.py`:

```python
@dataclass(frozen=True)
class SubmitAnswer:
    """No `elapsed_ms`. How long an answer took is a fact about the server's
    clock and the window it opened, and a client that reports its own would
    be reporting the tiebreak key it wants to win (`_rank_numeric`)."""

    actor_id: PlayerId
    deadline_id: DeadlineId
    value: AnswerValue
```

In `reducer.py`, above `_record_answer`:

```python
def elapsed_ms_for(deadline: Deadline, rules: GameRules, now: datetime) -> int:
    """How far into its window this answer landed, clamped to it.

    The window opened at `deadline_at - answer_timeout_ms`: every ANSWER
    deadline in the ruleset is allocated that way (`_open_expansion_question`,
    `_decide_target`, `_open_tiebreak`, `_open_final_tiebreak`), so the
    opening instant needs no new state to reconstruct.

    Clamped at both ends. Below zero the value would sort ahead of every
    honest answer, which is the cheat this exists to remove; above the
    timeout it would not be comparable with answers from other windows,
    since `_rank_numeric` compares elapsed values across a single window
    only after the window length has already bounded them.
    """
    opened_at = deadline.deadline_at - timedelta(milliseconds=rules.answer_timeout_ms)
    elapsed = int((now - opened_at).total_seconds() * 1000)
    return max(0, min(rules.answer_timeout_ms, elapsed))
```

`_record_answer` takes the two extra values it now needs:

```python
def _record_answer(
    turn: ExpansionQuestion | BattleDuel | BattleTiebreak | NeutralChallenge | FinalTiebreak,
    command: SubmitAnswer,
    rules: GameRules,
    ctx: DecisionContext,
) -> ev.AnswerSubmitted | None:
    """None means 'ignore' — an identical resubmission."""
    existing = turn.answers.get(command.actor_id)
    submitted = SubmittedAnswer(command.value, elapsed_ms_for(turn.deadline, rules, ctx.now))
    ...
```

The duplicate check compares `existing.value == submitted.value` and is unaffected: a resubmission of the same value is still ignored, even though the second submission's elapsed time differs — which is correct, because ignoring it means nothing is recorded at all.

Update all four call sites of `_record_answer` to pass `state.rules, ctx`.

- [ ] **Step 4: Update every `SubmitAnswer` construction in the test suite**

Run `grep -rn "SubmitAnswer(" backend/tests | grep -v test_answer_clock` and drop the `elapsed_ms` argument from each, positional or keyword. Tests that were asserting on a specific `elapsed_ms` in a resulting `SubmittedAnswer` now assert on the value derived from their own `ctx.now`; where a test relied on ordering two answers, set the two `DecisionContext(now=...)` values apart instead.

- [ ] **Step 5: Run the domain and runtime suites**

Run: `cd backend && uv run pytest tests/domain tests/runtime -q -m "not integration"`
Expected: PASS

- [ ] **Step 6: Confirm the reducer's coverage gate still reads 100 %**

Run: `cd backend && uv run pytest tests/domain -q`
Expected: PASS with `reducer.py` at 100 % branch coverage. `elapsed_ms_for`'s two clamps are both branches; the two clamp tests above cover them and the ordinary case covers the third.

- [ ] **Step 7: Run everything, including integration**

Run: `cd backend && docker compose -f docker-compose.test.yml up -d && uv run pytest -q`
Expected: PASS. Nothing persisted changed — `AnswerSubmitted` and `SubmittedAnswer` are untouched — so the golden corpus and every codec test are unaffected. If a golden test fails here, the change leaked into an *event*, which it must not.

- [ ] **Step 8: Commit**

```bash
git add backend/src/triviador/domain/game backend/tests
git commit -m "fix(domain): the server measures how long an answer took, not the client"
```

---

## Task 3: One envelope, and the handlers that make it total

**Files:**
- Create: `backend/src/triviador/api/errors.py`, `backend/src/triviador/api/schemas/__init__.py`, `backend/src/triviador/api/schemas/errors.py`
- Test: `backend/tests/api/test_envelope.py`

**Interfaces:**
- Consumes: `RejectedCommand`, `RejectCode` (`domain/game/actions.py`); `RuntimeCode` (`services/ports.py`); `ServerBusy`, `ServerRestarting`, `GameRecovering`, `GameUnrecoverable`, `RuntimeClosed` (`runtime/errors.py`).
- Produces: `ApiErrorCode` (StrEnum); `ErrorEnvelope` (Pydantic model, fields `code: ApiErrorCode | RejectCode`, `message: str`, `details: dict[str, Any] | None`); `ApiError(code, status, message, details=None)`; `install_error_handlers(app)`; `request_id_var: ContextVar[str]`.

- [ ] **Step 1: Write the failing test**

`backend/tests/api/test_envelope.py`:

```python
"""§6.3: every source of failure leaves through one envelope.

The row that matters is the last one. Starlette emits its own shapes for
404, 405 and unhandled 500s, and those would reach the frontend's Zod
boundary as unparseable bodies — at which point the client cannot tell an
application error from a proxy being down, which is the one distinction
`apiFetch`'s transport error exists to preserve.
"""

from typing import Any

import httpx
import pytest
from fastapi import APIRouter, FastAPI
from pydantic import BaseModel
from sqlalchemy.exc import OperationalError

from triviador.api.errors import ApiErrorCode, ApiError, install_error_handlers
from triviador.domain.game.actions import RejectCode, RejectedCommand
from triviador.runtime.errors import GameRecovering, GameUnrecoverable, ServerBusy
from triviador.services.ports import RuntimeCode


class Body(BaseModel):
    name: str
    password: str


def probe_app() -> FastAPI:
    app = FastAPI()
    router = APIRouter()

    @router.post("/echo")
    async def echo(body: Body) -> dict[str, str]:
        return {"name": body.name}

    @router.get("/boom")
    async def boom() -> None:
        raise RuntimeError("connection to postgres://user:hunter2@db failed")

    @router.get("/rejected")
    async def rejected() -> None:
        raise RejectedCommand(RejectCode.NOT_ADJACENT, "'r7' is not adjacent")

    @router.get("/busy")
    async def busy() -> None:
        raise ServerBusy("queue is full")

    @router.get("/recovering")
    async def recovering() -> None:
        raise GameRecovering("game is recovering")

    @router.get("/unrecoverable")
    async def unrecoverable() -> None:
        raise GameUnrecoverable("stream will never decode")

    @router.get("/db-down")
    async def db_down() -> None:
        raise OperationalError("SELECT 1", {}, Exception("server closed the connection"))

    @router.get("/too-big")
    async def too_big() -> None:
        raise ApiError(ApiErrorCode.PAYLOAD_TOO_LARGE, 413, "body exceeds 1048576 bytes")

    app.include_router(router)
    install_error_handlers(app)
    return app


@pytest.fixture
def client() -> httpx.AsyncClient:
    # `raise_app_exceptions=False`: Starlette's ServerErrorMiddleware calls
    # our 500 handler, sends its response, and then *re-raises* so a real
    # server can log the traceback. Without this the unhandled-exception
    # test would see the RuntimeError instead of the response the handler
    # produced — testing the raise, not the envelope.
    transport = httpx.ASGITransport(app=probe_app(), raise_app_exceptions=False)
    return httpx.AsyncClient(transport=transport, base_url="http://t")


async def envelope(client: httpx.AsyncClient, method: str, path: str, **kw: Any) -> Any:
    response = await client.request(method, path, **kw)
    body = response.json()
    assert set(body) <= {"code", "message", "details"}, body
    assert isinstance(body["code"], str) and isinstance(body["message"], str)
    return response, body


async def test_the_two_code_enums_are_disjoint() -> None:
    """`code` is one closed union of `ApiErrorCode | RejectCode`. The moment
    a value appears in both, the union stops discriminating and a client's
    `switch` silently takes the wrong branch."""
    assert not ({c.value for c in ApiErrorCode} & {c.value for c in RejectCode})


async def test_every_runtime_code_is_also_an_api_error_code() -> None:
    """§6.3 maps all four to 503, so the envelope must be able to name them
    without inventing a parallel vocabulary."""
    for code in RuntimeCode:
        assert code.value in {c.value for c in ApiErrorCode}


async def test_a_validation_failure_is_422_and_never_echoes_the_input(
    client: httpx.AsyncClient,
) -> None:
    """Pydantic's own error list carries `input`. On a login body that is
    the password, and it would land in a response body and in whatever logs
    it — so the handler keeps `loc` and `type` and drops the rest."""
    response, body = await envelope(client, "POST", "/echo", json={"name": "n", "password": 5})
    assert response.status_code == 422
    assert body["code"] == ApiErrorCode.VALIDATION_FAILED
    assert "hunter" not in response.text
    assert body["details"] == {"fields": [{"loc": "body.password", "type": "string_type"}]}


async def test_a_missing_route_is_the_envelope_not_starlettes_shape(
    client: httpx.AsyncClient,
) -> None:
    response, body = await envelope(client, "GET", "/nope")
    assert response.status_code == 404
    assert body["code"] == ApiErrorCode.NOT_FOUND
    assert "detail" not in body


async def test_a_wrong_method_is_the_envelope(client: httpx.AsyncClient) -> None:
    response, body = await envelope(client, "DELETE", "/echo")
    assert response.status_code == 405
    assert body["code"] == ApiErrorCode.METHOD_NOT_ALLOWED


async def test_a_rejected_command_is_409_carrying_its_reject_code(
    client: httpx.AsyncClient,
) -> None:
    response, body = await envelope(client, "GET", "/rejected")
    assert response.status_code == 409
    assert body["code"] == RejectCode.NOT_ADJACENT


@pytest.mark.parametrize(
    ("path", "code"),
    [
        ("/busy", ApiErrorCode.SERVER_BUSY),
        ("/recovering", ApiErrorCode.GAME_RECOVERING),
        ("/unrecoverable", ApiErrorCode.GAME_UNRECOVERABLE),
        ("/db-down", ApiErrorCode.DATABASE_UNAVAILABLE),
    ],
)
async def test_every_temporary_condition_is_503_with_its_own_code(
    client: httpx.AsyncClient, path: str, code: ApiErrorCode
) -> None:
    response, body = await envelope(client, "GET", path)
    assert response.status_code == 503
    assert body["code"] == code


async def test_a_payload_too_large_is_413(client: httpx.AsyncClient) -> None:
    response, body = await envelope(client, "GET", "/too-big")
    assert response.status_code == 413
    assert body["code"] == ApiErrorCode.PAYLOAD_TOO_LARGE


async def test_an_unhandled_exception_is_500_and_carries_no_exception_text(
    client: httpx.AsyncClient,
) -> None:
    """The route raises a message containing a connection string with a
    password in it — the shape real exceptions actually have."""
    response, body = await envelope(client, "GET", "/boom")
    assert response.status_code == 500
    assert body["code"] == ApiErrorCode.INTERNAL_ERROR
    assert "hunter2" not in response.text
    assert "postgres" not in response.text
    assert "Traceback" not in response.text
    assert body["message"] == "internal error"


async def test_a_500_carries_the_request_id_so_the_log_can_be_found(
    client: httpx.AsyncClient,
) -> None:
    response, body = await envelope(client, "GET", "/boom")
    assert body["details"] is not None
    assert body["details"]["request_id"] == response.headers["x-request-id"]
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd backend && uv run pytest tests/api/test_envelope.py -v --no-cov`
Expected: FAIL — `ModuleNotFoundError: No module named 'triviador.api.errors'`

- [ ] **Step 3: Write the envelope model**

`backend/src/triviador/api/schemas/errors.py`:

```python
"""The one response shape every failure takes (§6.3, Spec 1 §11.1)."""

from typing import Any

from pydantic import BaseModel, ConfigDict

from triviador.api.errors import ApiErrorCode
from triviador.domain.game.actions import RejectCode


class ErrorEnvelope(BaseModel):
    """`code` is a closed union of two disjoint enums.

    `ApiErrorCode` is "the server could not, or would not, do this";
    `RejectCode` is "the domain refused this command". Keeping both in one
    field means a client switches once. `test_envelope.py` asserts the two
    value sets never overlap.
    """

    model_config = ConfigDict(extra="forbid")

    code: ApiErrorCode | RejectCode
    message: str
    details: dict[str, Any] | None = None
```

- [ ] **Step 4: Write the codes and the handlers**

`backend/src/triviador/api/errors.py`:

```python
"""`ApiErrorCode`, `ApiError`, and the handlers that leave no other exit.

Registering a handler for bare `Exception` is what makes this total.
Without it Starlette's `ServerErrorMiddleware` emits `Internal Server
Error` as `text/plain`, and the frontend's `apiFetch` — which parses every
body — would classify a real application failure as a transport error
(§6.3), losing the only fact that distinguishes "the backend answered"
from "the backend was never reached".
"""

import logging
from contextvars import ContextVar
from enum import StrEnum
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy.exc import SQLAlchemyError
from starlette.exceptions import HTTPException as StarletteHTTPException

from triviador.domain.game.actions import RejectCode, RejectedCommand
from triviador.runtime.errors import (
    GameRecovering,
    GameUnrecoverable,
    RuntimeClosed,
    ServerBusy,
    ServerRestarting,
)

logger = logging.getLogger(__name__)

# Set by the request-id middleware (Task 4). Declared here because the 500
# handler is the one place that *must* be able to read it even when every
# other part of the request failed, and a handler reaching into middleware
# state would invert the dependency.
request_id_var: ContextVar[str] = ContextVar("request_id", default="-")


class ApiErrorCode(StrEnum):
    """Every value here and every value in `RejectCode` share one namespace.
    The four `RuntimeCode` values are repeated verbatim rather than
    imported, so this enum is the single closed list codegen exports; a
    test asserts the two sets agree."""

    VALIDATION_FAILED = "validation_failed"
    UNAUTHENTICATED = "unauthenticated"
    FORBIDDEN = "forbidden"
    NOT_FOUND = "not_found"
    METHOD_NOT_ALLOWED = "method_not_allowed"
    PAYLOAD_TOO_LARGE = "payload_too_large"
    CREDENTIALS_INVALID = "credentials_invalid"
    INVITE_INVALID = "invite_invalid"
    USERNAME_TAKEN = "username_taken"
    MAP_UNKNOWN = "map_unknown"
    PRESET_UNKNOWN = "preset_unknown"
    NO_DEFAULT_PRESET = "no_default_preset"
    SERVER_BUSY = "server_busy"
    SERVER_RESTARTING = "server_restarting"
    GAME_RECOVERING = "game_recovering"
    GAME_UNRECOVERABLE = "game_unrecoverable"
    DATABASE_UNAVAILABLE = "database_unavailable"
    INTERNAL_ERROR = "internal_error"


class ApiError(Exception):
    """A failure a route raises deliberately, already carrying its status."""

    def __init__(
        self,
        code: ApiErrorCode,
        status: int,
        message: str,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code
        self.status = status
        self.message = message
        self.details = details


_STATUS_CODES: dict[int, ApiErrorCode] = {
    401: ApiErrorCode.UNAUTHENTICATED,
    403: ApiErrorCode.FORBIDDEN,
    404: ApiErrorCode.NOT_FOUND,
    405: ApiErrorCode.METHOD_NOT_ALLOWED,
    413: ApiErrorCode.PAYLOAD_TOO_LARGE,
}

_TEMPORARY: dict[type[Exception], ApiErrorCode] = {
    ServerBusy: ApiErrorCode.SERVER_BUSY,
    ServerRestarting: ApiErrorCode.SERVER_RESTARTING,
    GameRecovering: ApiErrorCode.GAME_RECOVERING,
    GameUnrecoverable: ApiErrorCode.GAME_UNRECOVERABLE,
    # A runtime that closed under a caller is a retry, not a failure: the
    # caller re-`get()`s the game (§5.6). REST has no way to express "try
    # again immediately" other than 503, and the client's own backoff is
    # already correct for it.
    RuntimeClosed: ApiErrorCode.SERVER_BUSY,
}


def envelope(
    status: int,
    code: ApiErrorCode | RejectCode,
    message: str,
    details: dict[str, Any] | None = None,
) -> JSONResponse:
    body: dict[str, Any] = {"code": str(code), "message": message}
    if details is not None:
        body["details"] = details
    response = JSONResponse(status_code=status, content=body)
    # Set here, not only in the middleware: the 500 body carries the id in
    # `details`, and the two must agree even for a response the middleware
    # never gets to touch. Reads `"-"` until Task 4 installs the middleware
    # that sets the ContextVar — at which point both sides become a real
    # id together.
    response.headers["X-Request-Id"] = request_id_var.get()
    return response


def install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(ApiError)
    async def _api_error(_: Request, exc: ApiError) -> JSONResponse:
        return envelope(exc.status, exc.code, exc.message, exc.details)

    @app.exception_handler(RejectedCommand)
    async def _rejected(_: Request, exc: RejectedCommand) -> JSONResponse:
        # §6.3: "one case among these, not the privileged one."
        return envelope(409, exc.code, exc.message)

    @app.exception_handler(RequestValidationError)
    async def _validation(_: Request, exc: RequestValidationError) -> JSONResponse:
        # `loc` and `type` only. Pydantic's own entries carry `input`, which
        # on a login body is the password and on a command frame is the
        # answer — both of which §10.10 forbids emitting.
        fields = [
            {"loc": ".".join(str(p) for p in error["loc"]), "type": error["type"]}
            for error in exc.errors()
        ]
        return envelope(422, ApiErrorCode.VALIDATION_FAILED, "request failed validation",
                        {"fields": fields})

    @app.exception_handler(SQLAlchemyError)
    async def _database(_: Request, exc: SQLAlchemyError) -> JSONResponse:
        logger.error("database unavailable: %s", type(exc).__name__)
        return envelope(503, ApiErrorCode.DATABASE_UNAVAILABLE, "database unavailable")

    @app.exception_handler(StarletteHTTPException)
    async def _http(_: Request, exc: StarletteHTTPException) -> JSONResponse:
        code = _STATUS_CODES.get(exc.status_code, ApiErrorCode.INTERNAL_ERROR)
        # `exc.detail` is ours or Starlette's ("Not Found"), never an
        # exception message, so it is safe to pass through.
        return envelope(exc.status_code, code, str(exc.detail))

    for exc_type, api_code in _TEMPORARY.items():
        _install_temporary(app, exc_type, api_code)

    @app.exception_handler(Exception)
    async def _unhandled(_: Request, exc: Exception) -> JSONResponse:
        # The full exception goes to the log; the body gets a stable code, a
        # generic message, and the id that ties the two together (§6.3).
        request_id = request_id_var.get()
        logger.exception("unhandled exception (request_id=%s)", request_id)
        return envelope(500, ApiErrorCode.INTERNAL_ERROR, "internal error",
                        {"request_id": request_id})


def _install_temporary(app: FastAPI, exc_type: type[Exception], code: ApiErrorCode) -> None:
    """A closure per type, so `code` is bound at registration rather than
    read from a loop variable when the handler eventually runs."""

    @app.exception_handler(exc_type)
    async def _handler(_: Request, exc: Exception) -> JSONResponse:
        return envelope(503, code, str(exc))
```

- [ ] **Step 5: Run the envelope tests**

Run: `cd backend && uv run pytest tests/api/test_envelope.py -v --no-cov`
Expected: PASS (12 tests). For the last one to pass now, `envelope()` must already set the `X-Request-Id` response header from `request_id_var` — which reads `"-"` until Task 4 installs the middleware that sets it. Both sides of that assertion are therefore `"-"` today and a real id from Task 4 onwards, and the test is meaningful in both states: it asserts the body and the header name the *same* request.

- [ ] **Step 6: Commit**

```bash
git add backend/src/triviador/api backend/tests/api/test_envelope.py
git commit -m "feat(api): one error envelope, and handlers that leave no other exit"
```

---

## Task 4: Structured logging, the request id, and structural redaction

**Files:**
- Create: `backend/src/triviador/api/logging.py`
- Test: `backend/tests/api/test_logging.py`

**Interfaces:**
- Consumes: `Settings.log_level`, `Settings.log_format`; `request_id_var` (Task 3).
- Produces: `configure_logging(settings)`; `RequestContextMiddleware`; `REDACTED_KEYS: frozenset[str]`; `redact_processor(logger, name, event_dict)`.

- [ ] **Step 1: Write the failing test**

`backend/tests/api/test_logging.py`:

```python
"""§10.10: which fields are emitted, not which bytes appear.

Byte-scanning is the wrong test and §12.3 says so: an MC question's correct
answer is legitimate choice text, and a numeric answer can coincide with a
number in the prompt. So the guarantee is that the *keys* never leave.
"""

import json
import logging

import httpx
import pytest
import structlog
from fastapi import APIRouter, FastAPI

from triviador.api.errors import install_error_handlers, request_id_var
from triviador.api.logging import REDACTED_KEYS, RequestContextMiddleware, configure_logging


@pytest.fixture(autouse=True)
def json_logging(caplog: pytest.LogCaptureFixture) -> None:
    configure_logging(log_level="INFO", log_format="json")
    caplog.set_level(logging.INFO)


def emitted(caplog: pytest.LogCaptureFixture) -> list[dict[str, object]]:
    return [json.loads(record.message) for record in caplog.records]


@pytest.mark.parametrize("key", sorted(REDACTED_KEYS))
def test_every_forbidden_key_is_replaced_rather_than_emitted(
    caplog: pytest.LogCaptureFixture, key: str
) -> None:
    structlog.get_logger().info("probe", **{key: "hunter2"})
    (event,) = emitted(caplog)
    assert event[key] == "[redacted]"
    assert "hunter2" not in json.dumps(event)


def test_redaction_reaches_into_nested_structures(caplog: pytest.LogCaptureFixture) -> None:
    """A command frame arrives as one nested object; logging it whole is
    exactly the mistake §10.10 forbids, and a top-level-only redactor would
    not notice."""
    structlog.get_logger().info("probe", frame={"type": "submit_answer",
                                                "payload": {"value": 42}})
    (event,) = emitted(caplog)
    assert event["frame"]["payload"] == "[redacted]"


def test_the_forbidden_keys_cover_every_category_10_10_names() -> None:
    for key in ("password", "password_hash", "token", "token_hash", "cookie",
                "authorization", "code", "invite_code", "answer", "value",
                "payload", "s3_secret_access_key"):
        assert key in REDACTED_KEYS


async def test_every_request_gets_an_id_that_reaches_both_the_log_and_the_header(
    caplog: pytest.LogCaptureFixture,
) -> None:
    app = FastAPI()
    router = APIRouter()

    @router.get("/ok")
    async def ok() -> dict[str, str]:
        structlog.get_logger().info("in-handler")
        return {"ok": "yes"}

    app.include_router(router)
    app.add_middleware(RequestContextMiddleware)
    install_error_handlers(app)

    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as client:
        response = await client.get("/ok")

    header = response.headers["x-request-id"]
    assert header
    in_handler = next(e for e in emitted(caplog) if e["event"] == "in-handler")
    assert in_handler["request_id"] == header


async def test_the_id_survives_an_unhandled_exception(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """§6.3 puts the request id in the 500 body so an operator can find the
    traceback. `ServerErrorMiddleware` builds that body *outside* every
    user middleware, so a ContextVar reset on the way out would leave the
    body carrying `"-"` — a value that matches nothing in any log."""
    app = FastAPI()

    @app.get("/boom")
    async def boom() -> None:
        raise RuntimeError("kaboom")

    app.add_middleware(RequestContextMiddleware)
    install_error_handlers(app)

    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as client:
        response = await client.get("/boom")

    assert response.status_code == 500
    request_id = response.json()["details"]["request_id"]
    assert request_id != "-"
    assert request_id == response.headers["x-request-id"]


async def test_a_client_supplied_request_id_is_not_trusted(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """An id echoed back from the request would let a client collide two
    unrelated requests in the log, or inject newlines into it."""
    app = FastAPI()
    app.add_middleware(RequestContextMiddleware)

    @app.get("/ok")
    async def ok() -> dict[str, str]:
        return {"id": request_id_var.get()}

    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as client:
        response = await client.get("/ok", headers={"X-Request-Id": "spoofed\nINJECTED"})
    assert response.json()["id"] != "spoofed\nINJECTED"
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd backend && uv run pytest tests/api/test_logging.py -v --no-cov`
Expected: FAIL — `ModuleNotFoundError: No module named 'triviador.api.logging'`

- [ ] **Step 3: Write the logging module**

`backend/src/triviador/api/logging.py`:

```python
"""structlog to stdout, one request id per request, and a redactor that
works on keys.

The redactor is a structlog *processor*, not a call-site discipline. A
discipline is a thing every future caller has to remember, and the one who
forgets is logging an exception payload at three in the morning.
"""

import logging
import sys
import uuid
from typing import Any, Literal

import structlog
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from triviador.api.errors import request_id_var

REDACTED = "[redacted]"

# Spec 1B §10.10's list, as keys. Deliberately broad — `value` and `answer`
# catch a submitted answer wherever it is nested, `code` catches an invite
# code, and the cost of over-redacting a field that happened to be named
# `value` is a log line that says `[redacted]`.
REDACTED_KEYS = frozenset(
    {
        "password", "password_hash", "new_password",
        "token", "token_hash", "session_token", "access_token",
        "cookie", "set-cookie", "authorization",
        "code", "invite_code", "code_hash",
        "answer", "answers", "value", "correct_value", "correct_choice_index",
        "payload", "frames", "body",
        "s3_access_key_id", "s3_secret_access_key", "garage_rpc_secret",
        "postgres_password", "database_url",
    }
)


def _redact(value: Any, depth: int = 0) -> Any:
    # Bounded: a log event is a dict, not a graph, and an unbounded walk
    # over a value someone accidentally logged is a way to hang the logger.
    if depth > 6:
        return REDACTED
    if isinstance(value, dict):
        return {
            k: REDACTED if k.lower() in REDACTED_KEYS else _redact(v, depth + 1)
            for k, v in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [_redact(v, depth + 1) for v in value]
    return value


def redact_processor(
    _logger: Any, _name: str, event_dict: dict[str, Any]
) -> dict[str, Any]:
    return _redact(event_dict)  # type: ignore[no-any-return]


def _add_request_id(_logger: Any, _name: str, event_dict: dict[str, Any]) -> dict[str, Any]:
    event_dict["request_id"] = request_id_var.get()
    return event_dict


def configure_logging(*, log_level: str, log_format: Literal["json", "console"]) -> None:
    renderer: Any = (
        structlog.processors.JSONRenderer()
        if log_format == "json"
        else structlog.dev.ConsoleRenderer()
    )
    structlog.configure(
        processors=[
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            _add_request_id,
            # Last before rendering: everything above may add fields, and a
            # redactor that runs early cannot see them.
            redact_processor,
            renderer,
        ],
        wrapper_class=structlog.make_filtering_bound_logger(
            logging.getLevelNamesMapping()[log_level.upper()]
        ),
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=False,
    )
    # The stdlib loggers `runtime/` and `db/` already use are routed to the
    # same stream, so a quarantine logged by `GameManager` and a request
    # logged here end up in one stdout stream (§10.10).
    logging.basicConfig(format="%(message)s", stream=sys.stdout, level=log_level.upper())


class RequestContextMiddleware:
    """One id per request, generated here and never read from the request.

    Pure ASGI, outermost, and it **does not reset the ContextVar**. Both
    are deliberate, and each fixes a way the id would otherwise be missing
    from exactly the responses that need it most:

    1. **No reset.** Starlette's `ServerErrorMiddleware` — the thing that
       runs the 500 handler — sits *outside* every user middleware. A
       `finally: request_id_var.reset(token)` runs while the exception is
       still unwinding, so by the time the 500 body is built the id is
       gone and §6.3's "a 500 body carries the request id" is a comment.
       Setting without resetting is safe because the var is overwritten at
       the top of every request; a connection serving keep-alive requests
       on one task sees the new id, never a stale one.
    2. **Outermost.** The origin, body-limit and host checks all answer
       without reaching a route. Registered inside them, this would hand
       out ids for successful requests and none for refused ones — the
       opposite of useful.

    A client-supplied `X-Request-Id` is ignored: echoing one back would let
    a caller collide two unrelated requests in the log, or inject a newline
    into a line-oriented stream.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] not in ("http", "websocket"):
            await self.app(scope, receive, send)
            return

        request_id = uuid.uuid4().hex
        request_id_var.set(request_id)
        # Also on the scope, so a handler can read it without depending on
        # context propagation across a task boundary.
        scope.setdefault("state", {})["request_id"] = request_id
        if scope["type"] == "websocket":
            await self.app(scope, receive, send)
            return

        status = 500

        async def send_with_id(message: Message) -> None:
            nonlocal status
            if message["type"] == "http.response.start":
                status = message["status"]
                headers = list(message.get("headers", []))
                if not any(k.lower() == b"x-request-id" for k, _ in headers):
                    headers.append((b"x-request-id", request_id.encode("latin-1")))
                message = {**message, "headers": headers}
            await send(message)

        try:
            await self.app(scope, receive, send_with_id)
        finally:
            # In the `finally` so a request that raised past every handler
            # is still logged — with its id, which is the only thread back
            # to the traceback the 500 handler wrote.
            structlog.get_logger().info(
                "request",
                method=scope.get("method"),
                path=scope.get("path"),
                status=status,
            )
```

Note the log line deliberately carries `path`, never the query string or the body: a query string is where a code or token ends up when someone takes a shortcut.

- [ ] **Step 4: Run the logging tests**

Run: `cd backend && uv run pytest tests/api/test_logging.py -v --no-cov`
Expected: PASS

- [ ] **Step 5: Confirm the envelope tests now see a real id**

Task 3's `envelope()` already stamps `X-Request-Id` from the ContextVar, so `test_a_500_carries_the_request_id_so_the_log_can_be_found` compared `"-"` with `"-"` and passed. With the middleware installed it compares two real ids. Re-run it and check the value is no longer `"-"`:

```bash
cd backend && uv run pytest tests/api/test_envelope.py -k request_id -v --no-cov
```

- [ ] **Step 6: Run both suites**

Run: `cd backend && uv run pytest tests/api -v --no-cov`
Expected: PASS

- [ ] **Step 7: Commit**

```bash
git add backend/src/triviador/api backend/tests/api
git commit -m "feat(api): structured logging, a per-request id, and key-based redaction"
```

---

## Task 5: Identity contracts, the widened ports, and the two hashes

**Files:**
- Create: `backend/src/triviador/services/identity.py`
- Modify: `backend/src/triviador/services/ports.py`
- Modify: `backend/src/triviador/domain/ids.py`
- Modify: `backend/src/triviador/db/repositories/games.py` (import `GameSummary` from `services.ports`)
- Create: `backend/src/triviador/db/security.py`
- Test: `backend/tests/services/test_identity.py`, `backend/tests/db/test_security.py`

**Interfaces:**
- Consumes: `GameRules`, `GameId`, `MapId`, `PlayerId`, `LoadedMap`, `MapRegistry`.
- Produces, in `services/identity.py`: `UserRole` (`PLAYER`/`ADMIN`); `AuthenticatedPrincipal(user_id, role, session_id)`; `UserRecord(user_id, username, display_name, role, is_active, password_hash)`; `RedeemOutcome` (`OK`/`INVITE_INVALID`/`USERNAME_TAKEN`); Protocols `PasswordHasher`, `UserStore`, `SessionStore`, `InviteStore`.
- Produces, in `services/ports.py`: `GameSummary` (moved here from `db/repositories/games.py`), `GameCatalogPort`, `PresetRecord`, `PresetPort`; `MapProvider.available()`.
- Produces, in `db/security.py`: `Argon2Hasher`, `new_token() -> str`, `token_digest(token) -> str`.
- Produces, in `domain/ids.py`: `UserId`, `SessionId`.

- [ ] **Step 1: Write the failing tests**

`backend/tests/services/test_identity.py` — a conformance test in the shape `tests/services/test_ports.py` already uses, so `mypy --strict` is what actually proves it:

```python
"""The auth ports, proved by construction rather than by assertion.

`tests/services/test_ports.py` established the pattern: a minimal class per
Protocol, assigned to a variable of the Protocol type. If the shape is
wrong, `mypy --strict` fails; the runtime assertions below only prove the
module imports and the enums hold the values the rest of the plan spells.
"""

from datetime import UTC, datetime

from triviador.domain.ids import SessionId, UserId
from triviador.services.identity import (
    AuthenticatedPrincipal,
    InviteStore,
    PasswordHasher,
    RedeemOutcome,
    SessionStore,
    UserRecord,
    UserRole,
    UserStore,
)

NOW = datetime(2026, 8, 18, 12, 0, tzinfo=UTC)


class NullHasher:
    def hash(self, password: str) -> str:
        return password

    def verify(self, password: str, hashed: str) -> bool:
        return password == hashed


class NullUsers:
    async def create(
        self,
        *,
        user_id: UserId,
        username: str,
        password_hash: str,
        display_name: str,
        role: UserRole,
    ) -> None: ...

    async def get(self, user_id: UserId) -> UserRecord | None:
        return None

    async def get_by_username(self, username: str) -> UserRecord | None:
        return None

    async def count_admins(self) -> int:
        return 0


class NullSessions:
    async def create(
        self, *, session_id: SessionId, user_id: UserId, token_hash: str, expires_at: datetime
    ) -> None: ...

    async def resolve(self, token_hash: str, *, now: datetime) -> AuthenticatedPrincipal | None:
        return None

    async def revoke(self, session_id: SessionId, *, at: datetime) -> None: ...

    async def revoke_for_user(self, user_id: UserId, *, at: datetime) -> tuple[SessionId, ...]:
        return ()


class NullInvites:
    async def redeem(
        self,
        *,
        code_hash: str,
        user_id: UserId,
        username: str,
        password_hash: str,
        display_name: str,
        now: datetime,
    ) -> RedeemOutcome:
        return RedeemOutcome.INVITE_INVALID


_hasher: PasswordHasher = NullHasher()
_users: UserStore = NullUsers()
_sessions: SessionStore = NullSessions()
_invites: InviteStore = NullInvites()


def test_the_roles_are_exactly_player_and_admin() -> None:
    assert {r.value for r in UserRole} == {"player", "admin"}


def test_a_principal_carries_the_session_it_came_from() -> None:
    """Not decoration: revoking one session must not log the user's other
    tabs out, so the id that authenticated *this* connection has to travel
    with it."""
    principal = AuthenticatedPrincipal(UserId("u1"), UserRole.PLAYER, SessionId("s1"))
    assert principal.session_id == SessionId("s1")


def test_redeeming_reports_which_of_the_two_things_went_wrong() -> None:
    assert {o.value for o in RedeemOutcome} == {"ok", "invite_invalid", "username_taken"}
```

`backend/tests/db/test_security.py` — no database, so it lives outside the integration marker:

```python
"""Two hashes with two different jobs."""

from triviador.db.security import Argon2Hasher, new_token, token_digest


def test_a_password_verifies_against_its_own_hash() -> None:
    hasher = Argon2Hasher()
    hashed = hasher.hash("correct horse")
    assert hasher.verify("correct horse", hashed)


def test_a_wrong_password_is_false_rather_than_an_exception() -> None:
    """argon2-cffi raises on mismatch. A route that has to catch an
    exception to learn "wrong password" is a route that will eventually
    catch the wrong one and authenticate somebody."""
    hasher = Argon2Hasher()
    assert hasher.verify("wrong", hasher.hash("right")) is False


def test_a_corrupt_stored_hash_is_false_rather_than_a_500() -> None:
    assert Argon2Hasher().verify("anything", "not-a-hash") is False


def test_two_hashes_of_one_password_differ() -> None:
    hasher = Argon2Hasher()
    assert hasher.hash("same") != hasher.hash("same")


def test_tokens_are_unguessable_and_distinct() -> None:
    tokens = {new_token() for _ in range(100)}
    assert len(tokens) == 100
    assert all(len(t) >= 40 for t in tokens)


def test_a_token_digest_is_stable_and_hides_the_token() -> None:
    token = new_token()
    assert token_digest(token) == token_digest(token)
    assert token not in token_digest(token)
    assert len(token_digest(token)) == 64
```

- [ ] **Step 2: Run to verify they fail**

Run: `cd backend && uv run pytest tests/services/test_identity.py tests/db/test_security.py -v --no-cov`
Expected: FAIL — both modules are missing.

- [ ] **Step 3: Add the two id aliases**

In `backend/src/triviador/domain/ids.py`:

```python
# Identity, as distinct from participation. A user's `PlayerId` inside a
# game *is* their `UserId` — `games.host_id` and `game_players.user_id` are
# both foreign keys to `users.id` — but the two names carry different
# meanings and different lifetimes, and a signature that says `UserId` is
# saying "this is not scoped to a game".
UserId = NewType("UserId", str)
SessionId = NewType("SessionId", str)
```

- [ ] **Step 4: Write `services/identity.py`**

```python
"""Who a request is, and the three stores that can answer it.

Declared here for the same reason as `ports.py`: `api/` depends on these
Protocols, `db/` implements them, and neither imports the other. The
practical payoff is that Layer 3's contract suite runs against in-memory
fakes with no PostgreSQL and no argon2 — a suite that costs 50 ms of
deliberate key-stretching per login is a suite that gets skipped.
"""

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Protocol

from triviador.domain.ids import SessionId, UserId


class UserRole(StrEnum):
    PLAYER = "player"
    ADMIN = "admin"


@dataclass(frozen=True)
class AuthenticatedPrincipal:
    """What a session proves. Spec 1B §6.5: a connection stores *this*, not
    a `ViewerContext` — the socket is multiplexed and one connection can
    hold different standing in different topics."""

    user_id: UserId
    role: UserRole
    session_id: SessionId


@dataclass(frozen=True)
class UserRecord:
    user_id: UserId
    username: str
    display_name: str
    role: UserRole
    is_active: bool
    password_hash: str


class RedeemOutcome(StrEnum):
    OK = "ok"
    INVITE_INVALID = "invite_invalid"
    USERNAME_TAKEN = "username_taken"


class PasswordHasher(Protocol):
    """`verify` returns a bool and never raises on a mismatch or on a
    malformed stored hash: a caller that has to distinguish exceptions to
    learn "wrong password" eventually catches the wrong one."""

    def hash(self, password: str) -> str: ...
    def verify(self, password: str, hashed: str) -> bool: ...


class UserStore(Protocol):
    async def create(
        self,
        *,
        user_id: UserId,
        username: str,
        password_hash: str,
        display_name: str,
        role: UserRole,
    ) -> None: ...
    async def get(self, user_id: UserId) -> UserRecord | None: ...
    async def get_by_username(self, username: str) -> UserRecord | None: ...
    async def count_admins(self) -> int: ...


class SessionStore(Protocol):
    async def create(
        self, *, session_id: SessionId, user_id: UserId, token_hash: str, expires_at: datetime
    ) -> None: ...

    async def resolve(self, token_hash: str, *, now: datetime) -> AuthenticatedPrincipal | None:
        """Live session, unexpired, unrevoked, belonging to an active user.

        One method rather than four, because "this session is dead" has
        four causes and a caller that has to assemble them is a caller that
        forgets one. `users.is_active` is part of it: Spec 1 §7 requires
        that deactivating a user log them out *now*, which is the entire
        reason sessions are a table instead of a JWT.
        """
        ...

    async def revoke(self, session_id: SessionId, *, at: datetime) -> None: ...

    async def revoke_for_user(self, user_id: UserId, *, at: datetime) -> tuple[SessionId, ...]:
        """Returns the sessions it closed, so the caller can close their
        sockets with `4401` (§6.5). Plan 7's deactivate endpoint is that
        caller; this plan provides the half that can be tested now."""
        ...


class InviteStore(Protocol):
    async def redeem(
        self,
        *,
        code_hash: str,
        user_id: UserId,
        username: str,
        password_hash: str,
        display_name: str,
        now: datetime,
    ) -> RedeemOutcome:
        """Claim the invite and create the user, or neither.

        These are one method because they must be one transaction. Doing
        them in separate transactions burns an invite when the username
        turns out to be taken, or hands an account to whoever loses the
        race for the code.

        The implementation inserts the user, then claims with a conditional
        `UPDATE ... WHERE used_by IS NULL RETURNING id` — that order is
        forced, because `invite_codes.used_by` is a non-deferrable foreign
        key and a claim naming an absent user is rejected outright. The
        conditional UPDATE remains the concurrency check: two simultaneous
        redemptions of one code cannot both match it, and the loser's
        transaction takes its own user row down with it.
        """
        ...
```

- [ ] **Step 5: Widen `ports.py` and move `GameSummary`**

Cut the `GameSummary` dataclass out of `db/repositories/games.py` and paste it into `services/ports.py` unchanged (it already imports nothing but `domain`). In `games.py`, replace it with `from triviador.services.ports import GameSummary` — every existing import site keeps working, because `db.repositories.games.GameSummary` still resolves.

Then add, in `ports.py`:

```python
class GameCatalogPort(Protocol):
    """What the REST surface asks of the games table (§6.1, §6.2).

    Deliberately *not* a widening of `GameQueriesPort`. Widening that one
    would make every runtime fake — `tests/runtime/fakes.py` and three
    integration fixtures — grow three methods the runtime never calls, to
    satisfy a Protocol it only reads two of. `GameRepository` satisfies
    both, and neither consumer sees the other's surface.
    """

    async def create(
        self,
        *,
        game_id: GameId,
        map_id: MapId,
        rules: GameRules,
        host_id: PlayerId,
        map_sha256: str,
        preset_id: str | None,
        operation_id: str,
    ) -> None: ...
    async def get_summary(self, game_id: GameId) -> GameSummary | None: ...
    async def list_joinable(self) -> tuple[GameSummary, ...]: ...


@dataclass(frozen=True)
class PresetRecord:
    preset_id: str
    name: str
    rules: GameRules


class PresetPort(Protocol):
    """Read-only. Preset CRUD is Plan 7; `POST /api/games` only needs to
    resolve one id, or the default, into a frozen `GameRules`."""

    async def get(self, preset_id: str) -> PresetRecord | None: ...
    async def get_default(self) -> PresetRecord | None: ...
```

and one method on the existing `MapProvider`:

```python
class MapProvider(Protocol):
    def available(self) -> tuple[MapId, ...]: ...
    def load_with_digest(self, map_id: MapId) -> LoadedMap: ...
```

`MapRegistry` already has `available()`, so nothing implements a new method.

- [ ] **Step 6: Write `db/security.py`**

```python
"""Two hashes, chosen for two different threats.

**Passwords: argon2id.** They are low-entropy and human-chosen, so the only
defence against a stolen `users` table is making each guess expensive.

**Session and invite tokens: SHA-256.** They are 256 bits from
`secrets.token_urlsafe`, so there is nothing to guess and no dictionary to
try — the hash exists only so a leaked database row cannot be replayed as a
credential. Using argon2 here instead would put ~50 ms of deliberate
key-stretching on *every authenticated request*, which is a self-inflicted
outage rather than a security property. It also makes the token
unlookupable: an argon2 hash is salted, so a token could only be found by
scanning every session row and verifying each.
"""

import hashlib
import secrets

from argon2 import PasswordHasher as _Argon2
from argon2.exceptions import Argon2Error, InvalidHashError


class Argon2Hasher:
    """Implements `services.identity.PasswordHasher`."""

    def __init__(self) -> None:
        self._hasher = _Argon2()

    def hash(self, password: str) -> str:
        return self._hasher.hash(password)

    def verify(self, password: str, hashed: str) -> bool:
        try:
            return self._hasher.verify(hashed, password)
        except (Argon2Error, InvalidHashError):
            # A mismatch and a corrupt stored hash are both "no". The second
            # is not hypothetical: a truncated column, a hash written by a
            # different algorithm, or a row restored from a bad backup all
            # produce it, and a 500 there is an outage where a 401 is right.
            return False


def new_token() -> str:
    """32 bytes of `secrets` entropy, URL-safe: it rides in a cookie."""
    return secrets.token_urlsafe(32)


def token_digest(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()
```

- [ ] **Step 7: Run the tests, the layering gate, and mypy**

Run: `cd backend && uv run pytest tests/services tests/db/test_security.py tests/test_layering.py -v --no-cov && uv run mypy --strict`
Expected: PASS. `mypy` is the real assertion for Step 1's conformance classes; if a Protocol's shape is wrong, the four module-level annotated assignments fail here.

- [ ] **Step 8: Run the whole suite**

Run: `cd backend && uv run pytest -q -m "not integration"`
Expected: PASS — moving `GameSummary` changed no behaviour, and every existing import still resolves.

- [ ] **Step 9: Commit**

```bash
git add backend/src/triviador backend/tests
git commit -m "feat(services): identity contracts, the REST-side ports, and the two hashes"
```

---

## Task 6: The auth repositories, against real PostgreSQL

**Files:**
- Create: `backend/src/triviador/db/repositories/auth.py`, `backend/src/triviador/db/repositories/presets.py`
- Create: `backend/src/triviador/db/seed.py`, `backend/src/triviador/db/migrations/versions/0002_default_preset.py`
- Modify: `backend/tests/db/conftest.py` (add the `default_preset` fixture)
- Test: `backend/tests/db/test_auth_repositories.py`, `backend/tests/db/test_presets.py`

**Interfaces:**
- Consumes: `UserStore`, `SessionStore`, `InviteStore`, `PresetPort`, `PresetRecord`, `RedeemOutcome`, `UserRecord`, `AuthenticatedPrincipal`, `UserRole`; `db.models.auth.{User,Session,InviteCode}`; `db.models.presets.RulePreset`; `GameRules`, `validate_rules`, `DEFAULT_RULES`.
- Produces: `UserRepository(sessionmaker)`, `SessionRepository(sessionmaker)`, `InviteRepository(sessionmaker)`, `PresetRepository(sessionmaker)`; migration revision `0002_default_preset` inserting `rule_presets(id='default', is_default=true)`.

- [ ] **Step 1: Write the failing tests**

`backend/tests/db/test_auth_repositories.py`:

```python
"""The four ways a session is dead, and the one way an invite is claimed.

pytestmark is the integration pair this directory requires — see
`tests/db/conftest.py` for why the loop scope is not optional.
"""

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from triviador.db.models.auth import InviteCode, User
from triviador.db.repositories.auth import (
    InviteRepository,
    SessionRepository,
    UserRepository,
)
from triviador.domain.ids import SessionId, UserId
from triviador.services.identity import RedeemOutcome, UserRole

pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="session")]

NOW = datetime(2026, 8, 18, 12, 0, tzinfo=UTC)
LATER = NOW + timedelta(days=1)


async def a_user(sessions: async_sessionmaker[AsyncSession], **kw: object) -> UserId:
    user_id = UserId(str(kw.get("user_id", "u1")))
    await UserRepository(sessions).create(
        user_id=user_id,
        username=str(kw.get("username", "player")),
        password_hash="hash",
        display_name="Player",
        role=UserRole(str(kw.get("role", "player"))),
    )
    return user_id


async def a_session(
    sessions: async_sessionmaker[AsyncSession], user_id: UserId, **kw: object
) -> SessionId:
    session_id = SessionId(str(kw.get("session_id", "s1")))
    await SessionRepository(sessions).create(
        session_id=session_id,
        user_id=user_id,
        token_hash=str(kw.get("token_hash", "digest")),
        expires_at=kw.get("expires_at", LATER),  # type: ignore[arg-type]
    )
    return session_id


async def test_a_live_session_resolves_to_its_principal(clean_db, sessions) -> None:
    user_id = await a_user(sessions, role="admin")
    session_id = await a_session(sessions, user_id)
    principal = await SessionRepository(sessions).resolve("digest", now=NOW)
    assert principal is not None
    assert (principal.user_id, principal.role, principal.session_id) == (
        user_id,
        UserRole.ADMIN,
        session_id,
    )


async def test_an_unknown_token_resolves_to_nothing(clean_db, sessions) -> None:
    assert await SessionRepository(sessions).resolve("nope", now=NOW) is None


async def test_an_expired_session_resolves_to_nothing(clean_db, sessions) -> None:
    user_id = await a_user(sessions)
    await a_session(sessions, user_id, expires_at=NOW - timedelta(seconds=1))
    assert await SessionRepository(sessions).resolve("digest", now=NOW) is None


async def test_a_revoked_session_resolves_to_nothing(clean_db, sessions) -> None:
    user_id = await a_user(sessions)
    session_id = await a_session(sessions, user_id)
    await SessionRepository(sessions).revoke(session_id, at=NOW)
    assert await SessionRepository(sessions).resolve("digest", now=NOW) is None


async def test_a_deactivated_users_session_resolves_to_nothing(clean_db, sessions) -> None:
    """Spec 1 §7's whole argument for a session table instead of a JWT."""
    user_id = await a_user(sessions)
    await a_session(sessions, user_id)
    async with sessions() as session, session.begin():
        user = await session.get(User, user_id)
        assert user is not None
        user.is_active = False
    assert await SessionRepository(sessions).resolve("digest", now=NOW) is None


async def test_revoking_a_user_closes_every_session_and_names_them(clean_db, sessions) -> None:
    user_id = await a_user(sessions)
    await a_session(sessions, user_id, session_id="s1", token_hash="d1")
    await a_session(sessions, user_id, session_id="s2", token_hash="d2")
    closed = await SessionRepository(sessions).revoke_for_user(user_id, at=NOW)
    assert set(closed) == {SessionId("s1"), SessionId("s2")}
    assert await SessionRepository(sessions).resolve("d1", now=NOW) is None
    assert await SessionRepository(sessions).resolve("d2", now=NOW) is None


async def test_revoking_one_session_leaves_the_users_other_tabs_alone(clean_db, sessions) -> None:
    user_id = await a_user(sessions)
    await a_session(sessions, user_id, session_id="s1", token_hash="d1")
    await a_session(sessions, user_id, session_id="s2", token_hash="d2")
    await SessionRepository(sessions).revoke(SessionId("s1"), at=NOW)
    assert await SessionRepository(sessions).resolve("d2", now=NOW) is not None


async def test_a_user_is_found_by_username_and_by_id(clean_db, sessions) -> None:
    user_id = await a_user(sessions, username="alice")
    repo = UserRepository(sessions)
    by_name = await repo.get_by_username("alice")
    by_id = await repo.get(user_id)
    assert by_name == by_id
    assert by_name is not None and by_name.password_hash == "hash"


async def test_admins_are_counted_and_players_are_not(clean_db, sessions) -> None:
    await a_user(sessions, user_id="u1", username="a", role="admin")
    await a_user(sessions, user_id="u2", username="b", role="player")
    assert await UserRepository(sessions).count_admins() == 1


async def seed_invite(sessions: async_sessionmaker[AsyncSession], **kw: object) -> None:
    async with sessions() as session, session.begin():
        session.add(
            InviteCode(
                id=str(kw.get("id", "i1")),
                code_hash=str(kw.get("code_hash", "chash")),
                created_by=str(kw.get("created_by", "admin")),
                expires_at=kw.get("expires_at", LATER),  # type: ignore[arg-type]
                revoked_at=kw.get("revoked_at"),  # type: ignore[arg-type]
            )
        )


async def redeem(sessions: async_sessionmaker[AsyncSession], **kw: object) -> RedeemOutcome:
    return await InviteRepository(sessions).redeem(
        code_hash=str(kw.get("code_hash", "chash")),
        user_id=UserId(str(kw.get("user_id", "new"))),
        username=str(kw.get("username", "newbie")),
        password_hash="hash",
        display_name="Newbie",
        now=NOW,
    )


async def test_redeeming_a_valid_invite_creates_the_user_and_marks_it_used(
    clean_db, sessions
) -> None:
    await a_user(sessions, user_id="admin", username="admin", role="admin")
    await seed_invite(sessions)
    assert await redeem(sessions) == RedeemOutcome.OK
    created = await UserRepository(sessions).get_by_username("newbie")
    assert created is not None and created.role is UserRole.PLAYER
    async with sessions() as session:
        invite = await session.get(InviteCode, "i1")
        assert invite is not None and invite.used_by == "new"


async def test_a_second_redemption_of_one_invite_is_refused(clean_db, sessions) -> None:
    """The conditional UPDATE is the concurrency check as well as the
    business rule; this is its sequential half."""
    await a_user(sessions, user_id="admin", username="admin", role="admin")
    await seed_invite(sessions)
    assert await redeem(sessions) == RedeemOutcome.OK
    assert await redeem(sessions, user_id="other", username="other") == RedeemOutcome.INVITE_INVALID


@pytest.mark.parametrize(
    "invite",
    [
        {"expires_at": NOW - timedelta(seconds=1)},
        {"revoked_at": NOW},
        {"code_hash": "different"},
    ],
    ids=["expired", "revoked", "unknown"],
)
async def test_an_unusable_invite_is_refused(clean_db, sessions, invite: dict) -> None:
    await a_user(sessions, user_id="admin", username="admin", role="admin")
    await seed_invite(sessions, **invite)
    assert await redeem(sessions) == RedeemOutcome.INVITE_INVALID


async def test_a_taken_username_refuses_without_consuming_the_invite(clean_db, sessions) -> None:
    """The property the single-transaction design exists for: a redemption
    that fails on the username must leave the invite claimable, or a typo
    costs the invite."""
    await a_user(sessions, user_id="admin", username="taken", role="admin")
    await seed_invite(sessions)
    assert await redeem(sessions, username="taken") == RedeemOutcome.USERNAME_TAKEN
    async with sessions() as session:
        invite = await session.get(InviteCode, "i1")
        assert invite is not None and invite.used_by is None
    assert await redeem(sessions, username="fresh") == RedeemOutcome.OK
```

First, a fixture — because `clean_db` **truncates `rule_presets`** along with everything else, and the seeded default is schema rather than test data.

The tempting fix, excluding `rule_presets` from that `TRUNCATE`, is wrong and worth naming: it would preserve *mutations* between tests, so `test_an_inactive_preset_is_invisible` would leave `is_active = false` behind and every later test that creates a game would fail — depending on collection order, which is the worst kind of failing test. The fixture re-seeds a known baseline instead.

In `backend/tests/db/conftest.py`:

```python
@pytest_asyncio.fixture(loop_scope="session")
async def default_preset(
    clean_db: None, sessions: async_sessionmaker[AsyncSession]
) -> None:
    """Restore migration 0002's row after `clean_db` has truncated it.

    Depends on `clean_db` rather than replacing it: the point is a known
    baseline *before every test*, not surviving state. A test that
    deactivates the default gets a fresh active one next time, and nothing
    depends on the order tests happen to run in.

    The row is inserted from the migration's own frozen literal, so this
    fixture cannot drift from what a real database actually contains.
    """
    from triviador.db.seed import DEFAULT_PRESET_RULES

    async with sessions() as session, session.begin():
        session.add(
            RulePreset(
                id="default", name="Default", is_default=True,
                rules=dict(DEFAULT_PRESET_RULES), version=1, is_active=True,
            )
        )
```

`db/seed.py`, not the migration module: `0002_default_preset` starts with a digit and is therefore not importable by name, and reaching for `importlib.import_module` to work around that would couple a fixture to a filename. Both the migration and this fixture import the one frozen constant.

`backend/tests/db/test_presets.py`:

```python
"""The default preset, and the four ways a lookup can go."""

from dataclasses import asdict

import pytest
from sqlalchemy import select, update

from triviador.db.models.presets import RulePreset
from triviador.db.repositories.presets import PresetRepository
from triviador.db.seed import DEFAULT_PRESET_RULES
from triviador.domain.game.rules import DEFAULT_RULES, validate_rules

pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="session")]


def test_the_frozen_seed_is_a_valid_ruleset() -> None:
    """No database needed. What migration 0002 writes must be loadable, or
    every fresh installation is one `POST /api/games` away from a 500."""
    from triviador.db.repositories.presets import _to_rules

    assert validate_rules(_to_rules(dict(DEFAULT_PRESET_RULES))) == ()


def test_the_frozen_seed_still_matches_todays_defaults() -> None:
    """A drift alarm, not a duplication check.

    Migration 0002 froze these numbers deliberately (see its docstring), so
    this test failing is not a bug — it means someone changed
    `DEFAULT_RULES` and now has to decide what existing installations
    should do about it. Write migration `000N` to update them, then update
    the literal here.
    """
    assert dict(DEFAULT_PRESET_RULES) == asdict(DEFAULT_RULES)


async def test_exactly_one_default_preset_exists(default_preset, sessions) -> None:
    preset = await PresetRepository(sessions).get_default()
    assert preset is not None and preset.preset_id == "default"
    async with sessions() as session:
        rows = await session.execute(select(RulePreset).where(RulePreset.is_default))
        assert len(rows.all()) == 1


async def test_a_preset_is_reachable_by_id(default_preset, sessions) -> None:
    assert (await PresetRepository(sessions).get("default")) is not None
    assert (await PresetRepository(sessions).get("nope")) is None


async def test_an_inactive_preset_is_invisible(default_preset, sessions) -> None:
    """§6.1's soft deactivation. A retired preset must not be selectable for
    a new game, while `games.preset_id` on historical rows still resolves."""
    async with sessions() as session, session.begin():
        await session.execute(update(RulePreset).values(is_active=False))
    assert await PresetRepository(sessions).get("default") is None
    assert await PresetRepository(sessions).get_default() is None


async def test_the_previous_test_did_not_leak_its_deactivation(
    default_preset, sessions
) -> None:
    """Named for what it guards, because the failure it catches is
    order-dependent and therefore invisible in a normal run: if the fixture
    ever stops re-seeding, this is the test that says so."""
    assert await PresetRepository(sessions).get_default() is not None


async def test_rules_that_no_longer_validate_are_refused_rather_than_returned(
    default_preset, sessions
) -> None:
    """A preset row is JSONB written by an admin screen and by migrations
    across versions. Returning a `GameRules` that `validate_rules` rejects
    would push the failure into `decide`, which quarantines a runtime — so
    it fails here, where the caller can still answer 409."""
    async with sessions() as session, session.begin():
        await session.execute(
            update(RulePreset).values(rules={**asdict(DEFAULT_RULES), "player_count": 99})
        )
    with pytest.raises(ValueError):
        await PresetRepository(sessions).get("default")
```

- [ ] **Step 2: Run to verify they fail**

Run: `cd backend && docker compose -f docker-compose.test.yml up -d && uv run pytest tests/db/test_auth_repositories.py tests/db/test_presets.py -v --no-cov`
Expected: FAIL — the repository modules do not exist.

- [ ] **Step 3: Write the migration**

First `backend/src/triviador/db/seed.py` — append-only by the same rule the migrations are, so a fixture and a migration can share one literal without either being able to rewrite history:

```python
"""Values migrations wrote, frozen at the version that wrote them.

Nothing here may ever be edited in place. A migration is a record of what
a database was made to contain; editing a value it seeded changes what a
*fresh* installation gets while every upgraded installation keeps the old
one, and no row in either database records which it received. To change a
default, add a new constant and a new migration.
"""

DEFAULT_PRESET_RULES = {
    "player_count": 3,
    "expansion_rounds": 4,
    "battle_rounds": 4,
    "base_hp": 3,
    "answer_timeout_ms": 20000,
    "pick_timeout_ms": 15000,
    "warmup_ms": 5000,
    "claims_by_rank": [2, 1, 0],
    "pts_base": 1000,
    "pts_territory": 200,
    "pts_conquered": 400,
    "pts_defense": 100,
}
```

Then `backend/src/triviador/db/migrations/versions/0002_default_preset.py`:

```python
"""Seed the one default rule preset.

Spec 1 §7 makes the database enforce "at most one default" with a partial
unique index and leaves "never zero" to application logic. This *is* that
logic, applied at the only moment the system is guaranteed quiescent.
Doing it lazily at first use instead would mean two concurrent creates
racing to insert a default, which the partial unique index would then
turn into a 500 on one of them.

Revision ID: 0002_default_preset
Revises: 0001_initial
"""

import json

import sqlalchemy as sa
from alembic import op

revision = "0002_default_preset"
down_revision = "0001_initial"
branch_labels = None
depends_on = None

# Frozen, and imported from a frozen module rather than from
# `domain.game.rules`. A migration is a historical record of what a
# database was made to contain at one moment; `from ...rules import
# DEFAULT_RULES` would make this already-applied migration seed a
# *different* preset the day someone tunes the defaults, so a fresh
# install and an upgraded install would silently disagree about what
# `default` means with nothing in either database saying which it got.
# Changing the default later is a new migration — which is also the only
# form in which existing installations can be told about it.
from triviador.db.seed import DEFAULT_PRESET_RULES


def upgrade() -> None:
    op.execute(
        sa.text(
            "INSERT INTO rule_presets (id, name, is_default, rules, version, is_active) "
            "VALUES ('default', 'Default', true, :rules, 1, true)"
        ).bindparams(sa.bindparam("rules", json.dumps(DEFAULT_PRESET_RULES), type_=sa.JSON))
    )


def downgrade() -> None:
    op.execute("DELETE FROM rule_presets WHERE id = 'default'")
```

The duplication is the point, and one test keeps it from rotting silently: `test_the_frozen_seed_still_matches_todays_defaults` below asserts `DEFAULT_PRESET_RULES == asdict(DEFAULT_RULES)`. When someone deliberately changes `DEFAULT_RULES`, that test fails and tells them what they actually have to do — write migration `000N` for existing installations — instead of letting the two silently diverge by deployment date.

- [ ] **Step 4: Write the repositories**

`backend/src/triviador/db/repositories/auth.py` — the shape every method shares is `async with self._sessionmaker() as session[, session.begin()]`, exactly as `GameRepository` does.

```python
"""`UserRepository`, `SessionRepository`, `InviteRepository`.

Each implements the matching Protocol in `services/identity.py`. The only
non-obvious method is `InviteRepository.redeem`, which is one transaction
doing two things — see its docstring.
"""

from datetime import datetime

from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from triviador.db.models.auth import InviteCode, Session, User
from triviador.domain.ids import SessionId, UserId
from triviador.services.identity import (
    AuthenticatedPrincipal,
    RedeemOutcome,
    UserRecord,
    UserRole,
)


def _to_record(user: User) -> UserRecord:
    return UserRecord(
        user_id=UserId(user.id),
        username=user.username,
        display_name=user.display_name,
        role=UserRole(user.role),
        is_active=user.is_active,
        password_hash=user.password_hash,
    )


class UserRepository:
    def __init__(self, sessionmaker: async_sessionmaker[AsyncSession]) -> None:
        self._sessionmaker = sessionmaker

    async def create(
        self,
        *,
        user_id: UserId,
        username: str,
        password_hash: str,
        display_name: str,
        role: UserRole,
    ) -> None:
        async with self._sessionmaker() as session, session.begin():
            session.add(
                User(
                    id=user_id,
                    username=username,
                    password_hash=password_hash,
                    display_name=display_name,
                    role=str(role),
                    is_active=True,
                )
            )

    async def get(self, user_id: UserId) -> UserRecord | None:
        async with self._sessionmaker() as session:
            user = await session.get(User, user_id)
        return None if user is None else _to_record(user)

    async def get_by_username(self, username: str) -> UserRecord | None:
        async with self._sessionmaker() as session:
            result = await session.execute(select(User).where(User.username == username))
            user = result.scalar_one_or_none()
        return None if user is None else _to_record(user)

    async def count_admins(self) -> int:
        async with self._sessionmaker() as session:
            result = await session.execute(
                select(func.count())
                .select_from(User)
                .where(User.role == str(UserRole.ADMIN), User.is_active)
            )
            return result.scalar_one()


class SessionRepository:
    def __init__(self, sessionmaker: async_sessionmaker[AsyncSession]) -> None:
        self._sessionmaker = sessionmaker

    async def create(
        self, *, session_id: SessionId, user_id: UserId, token_hash: str, expires_at: datetime
    ) -> None:
        async with self._sessionmaker() as db, db.begin():
            db.add(
                Session(
                    id=session_id,
                    user_id=user_id,
                    token_hash=token_hash,
                    expires_at=expires_at,
                    revoked_at=None,
                )
            )

    async def resolve(self, token_hash: str, *, now: datetime) -> AuthenticatedPrincipal | None:
        """All four conditions in one statement.

        The join against `users` is what makes deactivation immediate: a
        second query would be a window in which a deactivated user's next
        request still succeeds.
        """
        async with self._sessionmaker() as db:
            result = await db.execute(
                select(Session.id, User.id, User.role)
                .join(User, User.id == Session.user_id)
                .where(
                    Session.token_hash == token_hash,
                    Session.revoked_at.is_(None),
                    Session.expires_at > now,
                    User.is_active,
                )
            )
            row = result.one_or_none()
        if row is None:
            return None
        session_id, user_id, role = row
        return AuthenticatedPrincipal(UserId(user_id), UserRole(role), SessionId(session_id))

    async def revoke(self, session_id: SessionId, *, at: datetime) -> None:
        async with self._sessionmaker() as db, db.begin():
            await db.execute(
                update(Session)
                .where(Session.id == session_id, Session.revoked_at.is_(None))
                .values(revoked_at=at)
            )

    async def revoke_for_user(self, user_id: UserId, *, at: datetime) -> tuple[SessionId, ...]:
        async with self._sessionmaker() as db, db.begin():
            result = await db.execute(
                update(Session)
                .where(Session.user_id == user_id, Session.revoked_at.is_(None))
                .values(revoked_at=at)
                .returning(Session.id)
            )
            return tuple(SessionId(i) for i in result.scalars().all())


class _InviteUnavailable(Exception):
    """The conditional claim matched no row.

    Private, and it never escapes `redeem`. It exists so the
    `IntegrityError` handler beside it can mean exactly one thing.
    """


class InviteRepository:
    def __init__(self, sessionmaker: async_sessionmaker[AsyncSession]) -> None:
        self._sessionmaker = sessionmaker

    async def redeem(
        self,
        *,
        code_hash: str,
        user_id: UserId,
        username: str,
        password_hash: str,
        display_name: str,
        now: datetime,
    ) -> RedeemOutcome:
        """Create and claim, or neither.

        **Insert first, claim second, and the order is forced.**
        `invite_codes.used_by` is a plain (non-deferrable) foreign key to
        `users.id`, so an `UPDATE` that names a user who does not exist yet
        is rejected by PostgreSQL the moment it runs — claiming first
        cannot work at all, whatever its other merits.

        The conditional `UPDATE ... WHERE used_by IS NULL RETURNING id` is
        still both the business rule and the concurrency control: two
        simultaneous redemptions of one code each insert their own user,
        then contend on that row, and exactly one gets a returned id. The
        loser raises `_InviteUnavailable`, which rolls its whole
        transaction back — its user row included, so no orphan account
        survives a lost race.

        A sentinel exception rather than an early `return`: `session.begin()`
        commits on a clean exit, so returning from inside it would commit
        the very user row we are trying to discard. Raising leaves the
        rollback to the context manager.

        The `flush()` is what keeps the `IntegrityError` handler honest. It
        forces the `INSERT` at a known point, so the only violation that
        handler can see is `users.username`'s UNIQUE constraint — which is
        why a typo costs a username and never an invite.
        """
        async with self._sessionmaker() as db:
            try:
                async with db.begin():
                    db.add(
                        User(
                            id=user_id,
                            username=username,
                            password_hash=password_hash,
                            display_name=display_name,
                            role=str(UserRole.PLAYER),
                            is_active=True,
                        )
                    )
                    await db.flush()
                    claimed = await db.execute(
                        update(InviteCode)
                        .where(
                            InviteCode.code_hash == code_hash,
                            InviteCode.used_by.is_(None),
                            InviteCode.revoked_at.is_(None),
                            InviteCode.expires_at > now,
                        )
                        .values(used_by=user_id, used_at=now)
                        .returning(InviteCode.id)
                    )
                    if claimed.scalar_one_or_none() is None:
                        raise _InviteUnavailable
            except _InviteUnavailable:
                return RedeemOutcome.INVITE_INVALID
            except IntegrityError:
                return RedeemOutcome.USERNAME_TAKEN
        return RedeemOutcome.OK
```

`backend/src/triviador/db/repositories/presets.py`:

```python
"""Read-only preset lookup. CRUD is Plan 7."""

from dataclasses import fields

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from triviador.db.models.presets import RulePreset
from triviador.domain.game.rules import GameRules, validate_rules
from triviador.services.ports import PresetRecord


def _to_rules(raw: dict[str, object]) -> GameRules:
    """JSONB back into the frozen dataclass, then validated.

    `claims_by_rank` round-trips through JSON as a list; `GameRules` is
    compared by value in tests and hashed nowhere, but a list where a tuple
    belongs is a shape difference that shows up much later as an inequality
    nobody expects.
    """
    names = {f.name for f in fields(GameRules)}
    missing = names - set(raw)
    if missing:
        raise ValueError(f"preset rules are missing {sorted(missing)}")
    kwargs = {k: v for k, v in raw.items() if k in names}
    kwargs["claims_by_rank"] = tuple(kwargs["claims_by_rank"])  # type: ignore[arg-type]
    rules = GameRules(**kwargs)  # type: ignore[arg-type]
    problems = validate_rules(rules)
    if problems:
        raise ValueError("preset rules are invalid: " + "; ".join(problems))
    return rules


class PresetRepository:
    def __init__(self, sessionmaker: async_sessionmaker[AsyncSession]) -> None:
        self._sessionmaker = sessionmaker

    async def get(self, preset_id: str) -> PresetRecord | None:
        return await self._one(RulePreset.id == preset_id)

    async def get_default(self) -> PresetRecord | None:
        return await self._one(RulePreset.is_default)

    async def _one(self, criterion: object) -> PresetRecord | None:
        async with self._sessionmaker() as session:
            result = await session.execute(
                select(RulePreset).where(criterion, RulePreset.is_active)  # type: ignore[arg-type]
            )
            preset = result.scalar_one_or_none()
        if preset is None:
            return None
        return PresetRecord(preset.id, preset.name, _to_rules(preset.rules))
```

- [ ] **Step 5: Run the integration tests**

Run: `cd backend && uv run pytest tests/db -v -m integration`
Expected: PASS. Run them twice in both orders to prove the isolation actually holds — the failure this guards against only appears when one test's mutation outlives it:

```bash
uv run pytest tests/db/test_presets.py -v -m integration
uv run pytest tests/db/test_presets.py -v -m integration -p no:randomly --reverse 2>/dev/null ||   uv run pytest tests/db/test_presets.py -v -m integration
```

- [ ] **Step 6: Run everything**

Run: `cd backend && uv run pytest -q && uv run ruff check && uv run mypy --strict`
Expected: PASS

- [ ] **Step 7: Commit**

```bash
git add backend/src/triviador/db backend/tests/db
git commit -m "feat(db): auth repositories, preset lookup, and the seeded default preset"
```

---

## Task 7: The app factory, the principal dependency, and the four auth routes

**Files:**
- Create: `backend/src/triviador/api/deps.py`, `backend/src/triviador/api/app.py`
- Create: `backend/src/triviador/api/schemas/auth.py`, `backend/src/triviador/api/http/__init__.py`, `backend/src/triviador/api/http/auth.py`
- Test: `backend/tests/api/fakes.py`, `backend/tests/api/conftest.py`, `backend/tests/api/test_auth.py`

**Interfaces:**
- Consumes: `Settings`, `Clock`, `UserStore`, `SessionStore`, `InviteStore`, `PasswordHasher`, `RedeemOutcome`, `AuthenticatedPrincipal`, `UserRole`; `new_token`, `token_digest` (`db/security.py` — pure functions, no session, so `api/` importing them is not a layering hole).
- Produces: `AppDependencies` (a frozen dataclass, grown by Tasks 12 and 15); `create_app(deps) -> FastAPI`; `current_principal(request) -> AuthenticatedPrincipal` (401 if absent); `optional_principal(request) -> AuthenticatedPrincipal | None`; `deps_of(request) -> AppDependencies`; routes `POST /api/auth/redeem`, `POST /api/auth/login`, `POST /api/auth/logout`, `GET /api/auth/me`; schema `Me`.

- [ ] **Step 1: Write the fakes**

`backend/tests/api/fakes.py` — in-memory stores, one dict each. The suite's whole speed argument rests on these.

```python
"""In-memory stores for the Layer 3 contract suite.

Not a shortcut: §12.3's tests are about the *contract* — envelopes, status
codes, strictness, actor derivation, close codes — and none of that is a
property of PostgreSQL. Running them against a database and argon2 would
add a container and ~50 ms per login to a suite whose value depends on
being run on every change.
"""

import hashlib
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta

from triviador.domain.ids import SessionId, UserId
from triviador.services.identity import (
    AuthenticatedPrincipal,
    RedeemOutcome,
    UserRecord,
    UserRole,
)

T0 = datetime(2026, 8, 18, 12, 0, tzinfo=UTC)


class FakeClock:
    """The API's own clock. Distinct from `tests/runtime/fakes.FakeClock`,
    which additionally drives `sleep_until` for the consumer loop; nothing
    in the HTTP layer sleeps."""

    def __init__(self, now: datetime = T0) -> None:
        self._now = now

    def now(self) -> datetime:
        return self._now

    async def sleep_until(self, when: datetime) -> None:
        self._now = max(self._now, when)

    def advance(self, delta: timedelta) -> None:
        self._now += delta


class FakeDatabase:
    """`DatabaseProbe`. `pings` exists so `test_liveness_never_touches_the
    _database` can assert the *absence* of a call rather than inferring it
    from a status code that would be 200 either way."""

    def __init__(self, reachable: bool = True) -> None:
        self.reachable = reachable
        self.pings = 0

    async def ping(self) -> bool:
        self.pings += 1
        return self.reachable


class FakeHasher:
    """A digest with a marker prefix, not the password with a prefix.

    `f"hashed:{password}"` would have made
    `test_a_stored_password_is_never_the_password` vacuously false — strip
    the prefix and the clear password is what is left. A digest keeps the
    assertion meaningful while costing microseconds instead of argon2's
    deliberate ~50 ms.
    """

    def hash(self, password: str) -> str:
        return "fake$" + hashlib.sha256(password.encode("utf-8")).hexdigest()

    def __init__(self) -> None:
        self.verifications = 0

    def verify(self, password: str, hashed: str) -> bool:
        self.verifications += 1
        return hashed == self.hash(password)


@dataclass
class FakeUsers:
    records: dict[UserId, UserRecord] = field(default_factory=dict)

    async def create(
        self, *, user_id: UserId, username: str, password_hash: str,
        display_name: str, role: UserRole,
    ) -> None:
        self.records[user_id] = UserRecord(user_id, username, display_name, role, True,
                                           password_hash)

    async def get(self, user_id: UserId) -> UserRecord | None:
        return self.records.get(user_id)

    async def get_by_username(self, username: str) -> UserRecord | None:
        return next((r for r in self.records.values() if r.username == username), None)

    async def count_admins(self) -> int:
        return sum(1 for r in self.records.values() if r.role is UserRole.ADMIN and r.is_active)

    def deactivate(self, user_id: UserId) -> None:
        self.records[user_id] = replace(self.records[user_id], is_active=False)


@dataclass
class FakeSessions:
    users: FakeUsers
    rows: dict[str, tuple[SessionId, UserId, datetime, datetime | None]] = field(
        default_factory=dict
    )

    async def create(
        self, *, session_id: SessionId, user_id: UserId, token_hash: str, expires_at: datetime
    ) -> None:
        self.rows[token_hash] = (session_id, user_id, expires_at, None)

    async def resolve(self, token_hash: str, *, now: datetime) -> AuthenticatedPrincipal | None:
        row = self.rows.get(token_hash)
        if row is None:
            return None
        session_id, user_id, expires_at, revoked_at = row
        user = self.users.records.get(user_id)
        if revoked_at is not None or expires_at <= now or user is None or not user.is_active:
            return None
        return AuthenticatedPrincipal(user_id, user.role, session_id)

    async def revoke(self, session_id: SessionId, *, at: datetime) -> None:
        for token_hash, (sid, uid, exp, rev) in list(self.rows.items()):
            if sid == session_id and rev is None:
                self.rows[token_hash] = (sid, uid, exp, at)

    async def revoke_for_user(self, user_id: UserId, *, at: datetime) -> tuple[SessionId, ...]:
        closed = []
        for token_hash, (sid, uid, exp, rev) in list(self.rows.items()):
            if uid == user_id and rev is None:
                self.rows[token_hash] = (sid, uid, exp, at)
                closed.append(sid)
        return tuple(closed)


@dataclass
class FakeInvites:
    users: FakeUsers
    valid: dict[str, bool] = field(default_factory=dict)  # code_hash -> unused

    async def redeem(
        self, *, code_hash: str, user_id: UserId, username: str, password_hash: str,
        display_name: str, now: datetime,
    ) -> RedeemOutcome:
        if not self.valid.get(code_hash, False):
            return RedeemOutcome.INVITE_INVALID
        if await self.users.get_by_username(username) is not None:
            # Mirrors the real repository: the claim rolls back with the
            # insert, so the invite stays usable.
            return RedeemOutcome.USERNAME_TAKEN
        self.valid[code_hash] = False
        await self.users.create(
            user_id=user_id, username=username, password_hash=password_hash,
            display_name=display_name, role=UserRole.PLAYER,
        )
        return RedeemOutcome.OK
```

`backend/tests/api/conftest.py`:

```python
"""The real ASGI app over fake adapters."""

from collections.abc import AsyncIterator

import httpx
import pytest
import pytest_asyncio

from tests.api.fakes import FakeClock, FakeHasher, FakeInvites, FakeSessions, FakeUsers
from triviador.api.app import create_app
from triviador.api.deps import AppDependencies
from triviador.config import Settings

ORIGIN = "http://box.lan"


@pytest.fixture
def settings() -> Settings:
    return Settings(  # type: ignore[call-arg]
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
```

- [ ] **Step 2: Write the failing auth test**

`backend/tests/api/test_auth.py`:

```python
"""§6.1's auth surface, and the cookie that carries it."""

import httpx
import pytest

from tests.api.fakes import FakeInvites, FakeSessions, FakeUsers
from triviador.api.errors import ApiErrorCode
from triviador.db.security import token_digest
from triviador.domain.ids import UserId
from triviador.services.identity import UserRole


async def register(client: httpx.AsyncClient, invites: FakeInvites, **kw: str) -> httpx.Response:
    # Keyed by the digest, not the literal: the route hashes the submitted
    # code before it reaches the store, so seeding a raw string here would
    # never match and every registration would fail as an invalid invite.
    invites.valid[token_digest(kw.get("code", "raw-code"))] = True
    return await client.post(
        "/api/auth/redeem",
        json={
            "code": kw.get("code", "raw-code"),
            "username": kw.get("username", "alice"),
            "password": kw.get("password", "correct horse"),
            "display_name": kw.get("display_name", "Alice"),
        },
    )


async def test_redeeming_a_valid_invite_creates_a_player_and_signs_them_in(
    client: httpx.AsyncClient, deps
) -> None:
    response = await register(client, deps.invites)
    assert response.status_code == 201
    assert response.json() == {
        "user_id": response.json()["user_id"],
        "username": "alice",
        "display_name": "Alice",
        "role": "player",
    }
    assert deps.settings.session_cookie_name in response.cookies


async def test_the_session_cookie_is_httponly_lax_and_matches_cookie_secure(
    client: httpx.AsyncClient, deps
) -> None:
    """A cookie readable from JavaScript is a session token in every XSS,
    and `SameSite=Lax` is half of §6.4's CSRF story — the other half is the
    origin check, which is why neither alone is enough."""
    response = await register(client, deps.invites)
    header = response.headers["set-cookie"].lower()
    assert "httponly" in header
    assert "samesite=lax" in header
    assert "secure" not in header  # cookie_secure=False in the fixture


async def test_the_cookie_is_marked_secure_when_the_deployment_says_so(deps) -> None:
    """The other half of the flag, and the half that fails silently.

    With only the `cookie_secure=False` branch covered, `secure=False`
    hardcoded would pass the whole suite — and a missing `Secure` sends the
    session token in cleartext the moment the deployment moves to HTTPS.
    §10.4 pairs `COOKIE_SECURE` with the origin scheme at startup for the
    same reason: getting it wrong produces a login that appears to succeed
    and then does nothing.
    """
    from triviador.api.app import create_app

    secure_deps = replace_deps(
        deps, settings=deps.settings.model_copy(update={"cookie_secure": True})
    )
    transport = httpx.ASGITransport(app=create_app(secure_deps), raise_app_exceptions=False)
    async with httpx.AsyncClient(
        transport=transport, base_url="http://testserver", headers={"Origin": ORIGIN}
    ) as client:
        response = await register(client, secure_deps.invites)

    header = response.headers["set-cookie"].lower()
    assert "secure" in header
    # The three flags are set in one call, so a refactor that breaks one
    # usually breaks the others — assert them together.
    assert "httponly" in header
    assert "samesite=lax" in header


async def test_a_bad_invite_is_401_and_creates_nobody(client: httpx.AsyncClient, deps) -> None:
    response = await client.post(
        "/api/auth/redeem",
        json={"code": "wrong", "username": "mallory", "password": "correct horse",
              "display_name": "M"},
    )
    assert response.status_code == 401
    assert response.json()["code"] == ApiErrorCode.INVITE_INVALID
    assert await deps.users.get_by_username("mallory") is None


async def test_a_taken_username_is_409(client: httpx.AsyncClient, deps) -> None:
    await register(client, deps.invites)
    deps.invites.valid[token_digest("raw-code")] = True
    response = await register(client, deps.invites)
    assert response.status_code == 409
    assert response.json()["code"] == ApiErrorCode.USERNAME_TAKEN


@pytest.mark.parametrize(
    "body",
    [
        {"username": "a", "password": "correct horse", "display_name": "A", "code": "c"},
        {"username": "alice", "password": "short", "display_name": "A", "code": "c"},
        {"username": "alice", "password": "correct horse", "display_name": "", "code": "c"},
        {"username": "al ice", "password": "correct horse", "display_name": "A", "code": "c"},
    ],
    ids=["username-too-short", "password-too-short", "empty-display-name", "username-has-space"],
)
async def test_a_malformed_registration_is_422(client: httpx.AsyncClient, body: dict) -> None:
    assert (await client.post("/api/auth/redeem", json=body)).status_code == 422


async def test_a_registration_carrying_a_role_is_rejected_outright(
    client: httpx.AsyncClient, deps
) -> None:
    """`extra="forbid"`, and the reason it is not optional: the field the
    request must never be able to set is the one that grants admin."""
    deps.invites.valid[token_digest("raw-code")] = True
    response = await client.post(
        "/api/auth/redeem",
        json={"code": "raw-code", "username": "mallory", "password": "correct horse",
              "display_name": "M", "role": "admin"},
    )
    assert response.status_code == 422


async def test_logging_in_returns_the_principal_and_a_cookie(
    client: httpx.AsyncClient, deps
) -> None:
    await register(client, deps.invites)
    client.cookies.clear()
    response = await client.post(
        "/api/auth/login", json={"username": "alice", "password": "correct horse"}
    )
    assert response.status_code == 200
    assert response.json()["username"] == "alice"
    assert deps.settings.session_cookie_name in response.cookies


@pytest.mark.parametrize(
    ("username", "password"),
    [("alice", "wrong"), ("nobody", "correct horse")],
    ids=["wrong-password", "unknown-user"],
)
async def test_both_kinds_of_bad_credentials_answer_identically(
    client: httpx.AsyncClient, deps, username: str, password: str
) -> None:
    """Identical code, identical message. A distinguishable response is a
    username oracle, and with argon2 on one path and nothing on the other
    the *timing* is an oracle too — which is why the route verifies against
    a dummy hash when the user does not exist."""
    await register(client, deps.invites)
    response = await client.post(
        "/api/auth/login", json={"username": username, "password": password}
    )
    assert response.status_code == 401
    assert response.json()["code"] == ApiErrorCode.CREDENTIALS_INVALID
    assert response.json()["message"] == "invalid username or password"


async def test_both_credential_failures_do_exactly_one_verification(
    client: httpx.AsyncClient, deps
) -> None:
    """The mitigation is "one `verify` on every path", not "some extra work
    on the short path". Counting the calls is the only way to assert it
    without timing anything, and timing assertions do not belong in a test
    suite."""
    await register(client, deps.invites)
    deps.hasher.verifications = 0
    await client.post("/api/auth/login", json={"username": "nobody", "password": "x"})
    unknown = deps.hasher.verifications
    deps.hasher.verifications = 0
    await client.post("/api/auth/login", json={"username": "alice", "password": "wrong"})
    assert unknown == deps.hasher.verifications == 1


async def test_me_returns_the_signed_in_user(client: httpx.AsyncClient, deps) -> None:
    await register(client, deps.invites)
    response = await client.get("/api/auth/me")
    assert response.status_code == 200
    assert response.json()["role"] == UserRole.PLAYER


async def test_me_without_a_cookie_is_401(client: httpx.AsyncClient) -> None:
    response = await client.get("/api/auth/me")
    assert response.status_code == 401
    assert response.json()["code"] == ApiErrorCode.UNAUTHENTICATED


async def test_logging_out_revokes_the_session_and_clears_the_cookie(
    client: httpx.AsyncClient, deps
) -> None:
    await register(client, deps.invites)
    response = await client.post("/api/auth/logout")
    assert response.status_code == 204
    assert (await client.get("/api/auth/me")).status_code == 401


async def test_a_deactivated_user_is_401_on_the_very_next_request(
    client: httpx.AsyncClient, deps
) -> None:
    """Spec 1 §7's requirement, at the layer that enforces it: no cache, no
    grace period, no waiting for the cookie to expire."""
    body = (await register(client, deps.invites)).json()
    deps.users.deactivate(UserId(body["user_id"]))
    assert (await client.get("/api/auth/me")).status_code == 401


async def test_an_expired_session_is_401(client: httpx.AsyncClient, deps) -> None:
    from datetime import timedelta

    await register(client, deps.invites)
    deps.clock.advance(timedelta(days=deps.settings.session_ttl_days + 1))
    assert (await client.get("/api/auth/me")).status_code == 401


async def test_a_stored_password_is_never_the_password(client: httpx.AsyncClient, deps) -> None:
    await register(client, deps.invites)
    record = await deps.users.get_by_username("alice")
    assert record is not None
    assert "correct horse" not in record.password_hash
```

The last test is deliberately weak against `FakeHasher` — it exists to fail loudly if a future refactor ever stores a raw password, and `tests/db/test_security.py` carries the real property.

- [ ] **Step 3: Run to verify it fails**

Run: `cd backend && uv run pytest tests/api/test_auth.py -v --no-cov`
Expected: FAIL — `ModuleNotFoundError: No module named 'triviador.api.app'`

- [ ] **Step 4: Write `deps.py`**

```python
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
from triviador.services.ports import Clock


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
```

- [ ] **Step 5: Write the auth schemas and routes**

`backend/src/triviador/api/schemas/auth.py`:

```python
from pydantic import BaseModel, ConfigDict, Field

from triviador.services.identity import UserRole

USERNAME = Field(min_length=3, max_length=32, pattern=r"^[A-Za-z0-9._-]+$")
PASSWORD = Field(min_length=8, max_length=256)
DISPLAY_NAME = Field(min_length=1, max_length=32)


class RedeemRequest(BaseModel):
    """`extra="forbid"` is load-bearing rather than tidy: the field this
    body must never be able to carry is `role`."""

    model_config = ConfigDict(extra="forbid")

    code: str = Field(min_length=1, max_length=128)
    username: str = USERNAME
    password: str = PASSWORD
    display_name: str = DISPLAY_NAME


class LoginRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    username: str = Field(min_length=1, max_length=32)
    password: str = Field(min_length=1, max_length=256)


class Me(BaseModel):
    model_config = ConfigDict(extra="forbid")

    user_id: str
    username: str
    display_name: str
    role: UserRole
```

`LoginRequest` deliberately does *not* reuse `USERNAME`/`PASSWORD`: a login must accept whatever the user has, including a password shorter than today's minimum, or raising the minimum silently locks out every existing account.

`backend/src/triviador/api/http/auth.py`:

```python
"""§6.1's auth surface. Four routes, one cookie."""

import uuid
from datetime import timedelta

from fastapi import APIRouter, Response

from triviador.api.deps import Deps, Principal
from triviador.api.errors import ApiError, ApiErrorCode
from triviador.api.schemas.auth import LoginRequest, Me, RedeemRequest
from triviador.db.security import new_token, token_digest
from triviador.domain.ids import SessionId, UserId
from triviador.services.identity import RedeemOutcome, UserRecord

router = APIRouter(prefix="/api/auth", tags=["auth"])


def _me(user: UserRecord) -> Me:
    return Me(
        user_id=str(user.user_id),
        username=user.username,
        display_name=user.display_name,
        role=user.role,
    )


async def _issue_session(deps: Deps, response: Response, user_id: UserId) -> None:
    token = new_token()
    expires_at = deps.clock.now() + timedelta(days=deps.settings.session_ttl_days)
    await deps.sessions.create(
        session_id=SessionId(uuid.uuid4().hex),
        user_id=user_id,
        token_hash=token_digest(token),
        expires_at=expires_at,
    )
    response.set_cookie(
        deps.settings.session_cookie_name,
        token,
        httponly=True,
        samesite="lax",
        secure=deps.settings.cookie_secure,
        max_age=deps.settings.session_ttl_days * 86_400,
        path="/",
    )


@router.post("/redeem", status_code=201)
async def redeem(body: RedeemRequest, response: Response, deps: Deps) -> Me:
    user_id = UserId(uuid.uuid4().hex)
    outcome = await deps.invites.redeem(
        code_hash=token_digest(body.code),
        user_id=user_id,
        username=body.username,
        password_hash=deps.hasher.hash(body.password),
        display_name=body.display_name,
        now=deps.clock.now(),
    )
    if outcome is RedeemOutcome.INVITE_INVALID:
        raise ApiError(ApiErrorCode.INVITE_INVALID, 401, "invite code is not usable")
    if outcome is RedeemOutcome.USERNAME_TAKEN:
        raise ApiError(ApiErrorCode.USERNAME_TAKEN, 409, "that username is taken")

    user = await deps.users.get(user_id)
    assert user is not None  # just created inside the same transaction
    await _issue_session(deps, response, user_id)
    return _me(user)


@router.post("/login")
async def login(body: LoginRequest, response: Response, deps: Deps) -> Me:
    user = await deps.users.get_by_username(body.username)
    if user is None or not user.is_active:
        # Exactly one `verify` against a hash computed once at startup —
        # the same work the found-user path does. Hashing here instead
        # would cost *two* argon2 operations on the unknown-user path and
        # one on the wrong-password path, which is the same oracle running
        # in the other direction and just as measurable with curl.
        deps.hasher.verify(body.password, deps.dummy_password_hash)
        raise ApiError(ApiErrorCode.CREDENTIALS_INVALID, 401, "invalid username or password")
    if not deps.hasher.verify(body.password, user.password_hash):
        raise ApiError(ApiErrorCode.CREDENTIALS_INVALID, 401, "invalid username or password")
    await _issue_session(deps, response, user.user_id)
    return _me(user)


@router.post("/logout", status_code=204)
async def logout(response: Response, deps: Deps, principal: Principal) -> None:
    await deps.sessions.revoke(principal.session_id, at=deps.clock.now())
    response.delete_cookie(deps.settings.session_cookie_name, path="/")


@router.get("/me")
async def me(deps: Deps, principal: Principal) -> Me:
    user = await deps.users.get(principal.user_id)
    if user is None:
        # The session resolved, so the row existed a moment ago. A user
        # deleted between the two reads is not a 500.
        raise ApiError(ApiErrorCode.UNAUTHENTICATED, 401, "not signed in")
    return _me(user)
```

- [ ] **Step 6: Write the app factory**

`backend/src/triviador/api/app.py`:

```python
"""The app factory. The *composition root* — which builds the real
adapters — is `build_app` in Task 15; this half only assembles routers,
handlers and middleware around a dependency bundle it is handed.

Split that way on purpose: every contract test in `tests/api/` constructs
an app over fakes, and a factory that reached for an engine could not be
called without a database.
"""

from fastapi import FastAPI

from triviador.api.deps import AppDependencies
from triviador.api.errors import install_error_handlers
from triviador.api.http import auth
from triviador.api.logging import RequestContextMiddleware


def create_app(deps: AppDependencies) -> FastAPI:
    app = FastAPI(title="Triviador", version="1", docs_url=None, redoc_url=None)
    app.state.deps = deps
    app.add_middleware(RequestContextMiddleware)
    app.include_router(auth.router)
    install_error_handlers(app)
    return app
```

`docs_url=None, redoc_url=None`: the OpenAPI document is exported for codegen (§7), not served. A LAN box does not need an interactive schema browser, and one that is served is one more surface to reason about.

- [ ] **Step 7: Run the auth tests**

Run: `cd backend && uv run pytest tests/api -v --no-cov`
Expected: PASS

- [ ] **Step 8: Commit**

```bash
git add backend/src/triviador/api backend/tests/api
git commit -m "feat(api): the app factory, the session principal, and the four auth routes"
```

---

## Task 8: Origin checking, trusted hosts, and a bounded body

**Files:**
- Create: `backend/src/triviador/api/middleware.py`
- Modify: `backend/src/triviador/api/app.py`
- Test: `backend/tests/api/test_origin.py`

**Interfaces:**
- Consumes: `Settings.allowed_origins`, `.allowed_hosts`, `.max_body_bytes`; `envelope`, `ApiErrorCode`.
- Produces: `OriginMiddleware(app, allowed_origins)`; `BodyLimitMiddleware(app, max_bytes)`; `origin_allowed(origin, allowed) -> bool` (reused by the WS handshake in Task 14).

- [ ] **Step 1: Write the failing test**

`backend/tests/api/test_origin.py`:

```python
"""§6.4: cookie auth with no CSRF token makes this load-bearing.

`SameSite=Lax` does not cover it on its own — a top-level POST navigation
from another site sends a Lax cookie — so an unsafe method with a foreign
or missing `Origin` is refused before it reaches a route.
"""

import httpx
import pytest

from tests.api.conftest import ORIGIN
from triviador.api.errors import ApiErrorCode
from triviador.api.middleware import origin_allowed


@pytest.mark.parametrize(
    ("origin", "allowed"),
    [
        ("http://box.lan", True),
        ("http://box.lan:5173", False),
        ("http://evil.lan", False),
        ("http://box.lan.evil.lan", False),
        ("null", False),
        ("", False),
    ],
)
def test_origin_matching_is_exact(origin: str, allowed: bool) -> None:
    """Exact string equality, not a prefix or a suffix. `box.lan.evil.lan`
    is the attack a `endswith` check waves through, and a port is part of
    an origin — `http://box.lan:5173` is a different origin from
    `http://box.lan`, which is why §10.4 requires both to be listed if both
    are used."""
    assert origin_allowed(origin, ("http://box.lan",)) is allowed


async def test_a_safe_method_needs_no_origin(client: httpx.AsyncClient) -> None:
    """A GET cannot be a CSRF write, and requiring an origin on reads would
    break a plain address-bar navigation."""
    response = await client.get("/api/auth/me", headers={"Origin": "http://evil.lan"})
    assert response.status_code == 401  # reached the route, refused on auth


@pytest.mark.parametrize("method", ["POST", "PUT", "PATCH", "DELETE"])
async def test_an_unsafe_method_from_a_foreign_origin_is_403(
    client: httpx.AsyncClient, method: str
) -> None:
    response = await client.request(
        method, "/api/auth/logout", headers={"Origin": "http://evil.lan"}
    )
    assert response.status_code == 403
    assert response.json()["code"] == ApiErrorCode.FORBIDDEN


async def test_an_unsafe_method_with_no_origin_at_all_is_403(client: httpx.AsyncClient) -> None:
    """A missing header is not a pass. Non-browser clients — curl, a script
    — simply send the header; a browser always does for cross-origin
    writes, and the same-origin case is the one the frontend produces."""
    response = await client.post("/api/auth/logout", headers={"Origin": ""})
    assert response.status_code == 403


async def test_an_unsafe_method_from_an_allowed_origin_reaches_the_route(
    client: httpx.AsyncClient,
) -> None:
    response = await client.post("/api/auth/logout", headers={"Origin": ORIGIN})
    assert response.status_code == 401  # reached the route, refused on auth


async def test_a_refusal_from_a_middleware_still_carries_a_request_id(
    client: httpx.AsyncClient,
) -> None:
    """The reason request-id is outermost. A 403 that no route produced is
    still a response an operator has to be able to find in the log."""
    response = await client.post("/api/auth/logout", headers={"Origin": "http://evil.lan"})
    assert response.status_code == 403
    assert response.headers["x-request-id"]


async def test_no_cors_headers_are_ever_emitted(client: httpx.AsyncClient) -> None:
    """§6.4: "CORS disabled". An `Access-Control-Allow-Origin` would invite
    exactly the cross-origin request the origin check exists to refuse."""
    response = await client.get("/api/auth/me")
    assert not [h for h in response.headers if h.lower().startswith("access-control-")]


async def test_a_body_over_the_limit_is_413(client: httpx.AsyncClient, deps) -> None:
    oversized = "x" * (deps.settings.max_body_bytes + 1)
    response = await client.post(
        "/api/auth/login", json={"username": "a", "password": oversized}
    )
    assert response.status_code == 413
    assert response.json()["code"] == ApiErrorCode.PAYLOAD_TOO_LARGE


async def test_a_chunked_body_over_the_limit_is_also_413(
    client: httpx.AsyncClient, deps
) -> None:
    """The one that matters. A chunked request declares no
    `Content-Length`, so the header check cannot see it — and a middleware
    that merely *counted* the bytes on their way to the route would have
    bounded nothing, because the route already has them. This is an
    unauthenticated path, so "the client is well behaved" is not an
    assumption available here.
    """

    async def oversized_chunks():
        for _ in range((deps.settings.max_body_bytes // 1024) + 2):
            yield b"x" * 1024

    response = await client.post("/api/auth/login", content=oversized_chunks())
    assert response.status_code == 413
    assert response.json()["code"] == ApiErrorCode.PAYLOAD_TOO_LARGE


async def test_a_body_under_the_limit_still_reaches_the_route_intact(
    client: httpx.AsyncClient,
) -> None:
    """The replay half: the middleware reads the body, so it must hand the
    route the same bytes. A silent truncation here would surface as a 422
    on a request that was perfectly valid."""
    response = await client.post(
        "/api/auth/login", json={"username": "alice", "password": "correct horse"}
    )
    assert response.status_code == 401  # reached the route, refused on credentials
    assert response.json()["code"] == ApiErrorCode.CREDENTIALS_INVALID


async def test_a_foreign_host_header_is_refused(deps) -> None:
    """`ALLOWED_HOSTS` (§10.4, §10.11). A DNS-rebinding page in a player's
    browser reaches a LAN service by name; checking `Host` is what stops
    it, and §10.11 asks for it at the edge *and* here."""
    from triviador.api.app import create_app

    transport = httpx.ASGITransport(app=create_app(deps), raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://evil.lan") as client:
        assert (await client.get("/api/auth/me")).status_code == 400
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd backend && uv run pytest tests/api/test_origin.py -v --no-cov`
Expected: FAIL — `triviador.api.middleware` does not exist.

- [ ] **Step 3: Write the middleware**

```python
"""Origin checking, and a body limit Starlette does not provide.

All three are pure ASGI rather than `BaseHTTPMiddleware`, which is also
what `RequestContextMiddleware` (Task 4) became. Two reasons, and both
have already produced a bug in this plan: a `BaseHTTPMiddleware` cannot
refuse a request before its body is read, and it runs the downstream app
in a *separate task*, which is what made the first version of the request
id vanish from exactly the 500 responses that document it.
"""

from collections.abc import Sequence

from starlette.types import ASGIApp, Message, Receive, Scope, Send

from triviador.api.errors import ApiErrorCode, envelope

SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})


def origin_allowed(origin: str, allowed: Sequence[str]) -> bool:
    """Exact match. Not a prefix (`http://box.lan` would pass
    `http://box.lan.evil.lan` under `startswith`), not a suffix, and not a
    parsed-host comparison that would discard the port."""
    return origin in allowed


class HostMiddleware:
    """`ALLOWED_HOSTS` (§10.4, §10.11), answering with the envelope.

    Starlette ships `TrustedHostMiddleware`, and it emits `text/plain`.
    That would be the single hole in "every response body is an envelope",
    and the hole matters more than the convenience: `apiFetch` parses every
    body and reports an unparseable one as a *transport* error — "the
    backend was never reached" — which is exactly the wrong diagnosis for a
    host the backend deliberately refused. Fifteen lines is cheaper than an
    exception to the contract.

    `"*"` disables the check, matching Starlette's behaviour so a
    development configuration does not have to enumerate every interface.
    """

    def __init__(self, app: ASGIApp, *, allowed_hosts: Sequence[str]) -> None:
        self.app = app
        self.allowed = tuple(allowed_hosts)
        self.any_host = "*" in self.allowed

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if self.any_host or scope["type"] not in ("http", "websocket"):
            await self.app(scope, receive, send)
            return
        headers = {k.decode("latin-1").lower(): v.decode("latin-1") for k, v in scope["headers"]}
        # The port is not part of the comparison: a LAN deployment is
        # reached on `:80` through Caddy and on `:8000` directly in
        # development, and both are the same host.
        host = headers.get("host", "").split(":")[0]
        if host in self.allowed:
            await self.app(scope, receive, send)
            return
        if scope["type"] == "websocket":
            # A pre-accept refusal *can* carry a response: Starlette's
            # `websocket.http.response` denial extension is exactly that,
            # and it is how this branch keeps the envelope contract the
            # class exists to uphold. A bare `websocket.close` here would
            # not: uvicorn turns a close sent before `accept` into a
            # hardcoded 403 with an empty `text/plain` body and discards
            # the application's close code entirely — so the code would be
            # unobservable and the body would be the one hole in "every
            # response is an envelope".
            #
            # Accepting first and then closing would be worse still: it
            # completes a handshake with a host we do not trust, purely to
            # hang up on it. (Task 16 *does* accept first, but for origin
            # and session — refusals that only make sense once we have
            # decided to talk to this client at all.)
            #
            # The fallback uses 1008 rather than 4403: 4403 is our
            # application-level "not authorized for topic" code and means
            # something only after a handshake completes. Putting it where
            # it is provably discarded would leave a number in the code
            # that no client can ever see.
            if "websocket.http.response" in scope.get("extensions", {}):
                response = envelope(400, ApiErrorCode.FORBIDDEN, "host not allowed")
                await response(scope, receive, send)
            else:
                await send({"type": "websocket.close", "code": 1008})
            return
        response = envelope(400, ApiErrorCode.FORBIDDEN, "host not allowed")
        await response(scope, receive, send)


class OriginMiddleware:
    """§6.4, for REST. The `/ws` half lives in the endpoint (Task 14),
    because a handshake is refused with a close code, not a status."""

    def __init__(self, app: ASGIApp, *, allowed_origins: Sequence[str]) -> None:
        self.app = app
        self.allowed = tuple(allowed_origins)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or scope["method"] in SAFE_METHODS:
            await self.app(scope, receive, send)
            return
        headers = {k.decode("latin-1").lower(): v.decode("latin-1") for k, v in scope["headers"]}
        if not origin_allowed(headers.get("origin", ""), self.allowed):
            response = envelope(403, ApiErrorCode.FORBIDDEN, "origin not allowed")
            await response(scope, receive, send)
            return
        await self.app(scope, receive, send)


class BodyLimitMiddleware:
    """413 above `max_bytes`, for a declared body *and* an undeclared one.

    The body is read to completion here, bounded, **before** the app is
    invoked, and replayed to it from memory. That is the part that has to
    be right: a chunked request carries no `Content-Length`, so counting
    bytes while streaming them onward bounds nothing — the app has already
    received them. Reading first means an oversized body is refused with
    at most `max_bytes + 1` held, and the route never starts.

    The cost is that every request is buffered. At 1 MiB and §1.1's two to
    four players that is not a tradeoff worth agonising over; Plan 7's
    media upload, which is the one genuinely large body in the system,
    needs a streaming route of its own and must exclude itself from this
    middleware rather than raise the cap for everybody.
    """

    def __init__(self, app: ASGIApp, *, max_bytes: int) -> None:
        self.app = app
        self.max_bytes = max_bytes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        headers = {k.decode("latin-1").lower(): v.decode("latin-1") for k, v in scope["headers"]}
        declared = headers.get("content-length")
        if declared is not None and declared.isdigit() and int(declared) > self.max_bytes:
            # The cheap path: refuse without reading a byte.
            await self._refuse(scope, receive, send)
            return

        chunks: list[bytes] = []
        total = 0
        more = True
        while more:
            message = await receive()
            if message["type"] == "http.disconnect":
                # The client went away mid-body. There is nobody to answer.
                return
            chunk: bytes = message.get("body", b"")
            total += len(chunk)
            if total > self.max_bytes:
                # Stop reading here: the remaining bytes are the client's
                # problem, and continuing to drain them is the DoS.
                await self._refuse(scope, receive, send)
                return
            chunks.append(chunk)
            more = bool(message.get("more_body", False))

        body = b"".join(chunks)
        delivered = False

        async def replay() -> Message:
            nonlocal delivered
            if delivered:
                # Anything after the single body message is the client
                # disconnecting; a route that reads twice must not hang.
                return {"type": "http.disconnect"}
            delivered = True
            return {"type": "http.request", "body": body, "more_body": False}

        await self.app(scope, replay, send)

    async def _refuse(self, scope: Scope, receive: Receive, send: Send) -> None:
        response = envelope(
            413, ApiErrorCode.PAYLOAD_TOO_LARGE, f"body exceeds {self.max_bytes} bytes"
        )
        await response(scope, receive, send)
```

- [ ] **Step 4: Install them in `create_app`**

Order matters and is asserted by the tests above.

```python
from triviador.api.middleware import BodyLimitMiddleware, HostMiddleware, OriginMiddleware


def create_app(deps: AppDependencies) -> FastAPI:
    app = FastAPI(title="Triviador", version="1", docs_url=None, redoc_url=None)
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
    install_error_handlers(app)
    return app
```

`HostMiddleware` rather than Starlette's `TrustedHostMiddleware`: the latter emits `text/plain`, which would be the one response in the system that is not an envelope — and the global constraint is not decoration, it is what lets `apiFetch` treat an unparseable body as "the backend was never reached". Extend the test to assert the shape as well as the status:

```python
async def test_a_foreign_host_header_is_refused_with_an_envelope(deps) -> None:
    transport = httpx.ASGITransport(app=create_app(deps), raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://evil.lan") as client:
        response = await client.get("/api/auth/me")
    assert response.status_code == 400
    assert response.json()["code"] == ApiErrorCode.FORBIDDEN


async def test_a_host_with_a_port_matches_the_bare_entry(deps) -> None:
    """`Host: testserver:8000` is the same host as `testserver`, and a
    development deploy reached directly rather than through Caddy sends
    exactly that."""
    transport = httpx.ASGITransport(app=create_app(deps), raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver:8000") as c:
        assert (await c.get("/api/auth/me")).status_code == 401
```

- [ ] **Step 5: Run the tests**

Run: `cd backend && uv run pytest tests/api -v --no-cov`
Expected: PASS. `test_auth.py` keeps passing because the conftest client sends `Origin` on every request.

- [ ] **Step 6: Commit**

```bash
git add backend/src/triviador/api backend/tests/api
git commit -m "feat(api): origin checking, trusted hosts, and a bounded request body"
```

---

## Task 9: The viewer, and a question DTO that cannot leak its answer

**Files:**
- Create: `backend/src/triviador/api/projection/viewer.py`
- Create: `backend/src/triviador/api/schemas/games.py` (the question half; the rest lands in Tasks 10–11)
- Test: `backend/tests/api/test_projection_question.py`

**Interfaces:**
- Consumes: `GameState`, `QuestionSnapshot`, `ChoiceSnapshot`, `QuestionKind`, `Difficulty`, `AuthenticatedPrincipal`, `UserRole`, `UserId`, `PlayerId`.
- Produces: `ViewerContext(user_id, player_id, role)`; `viewer_for(state, principal) -> ViewerContext`; `ClientChoice`, `ClientQuestion`, `RevealedAnswer`; `project_question(question, *, media_base) -> ClientQuestion`; `RevealedAnswer.of(question)` (a classmethod — there is no free `reveal_answer` function).

- [ ] **Step 1: Write the failing test**

`backend/tests/api/test_projection_question.py`:

```python
"""§8.7 and §12.3: the pre-resolution DTO does not *contain* the answer.

Not "sets it to None", not "excludes it on dump". The field is absent from
the model, so no serialization flag, no future `model_dump(exclude_none=
False)`, and no debug endpoint can put it back.
"""

from decimal import Decimal

import pytest

from tests.conftest import lobby_state, mc_question, numeric_question
from triviador.api.projection.viewer import viewer_for
from triviador.api.schemas.games import ClientQuestion, RevealedAnswer, project_question
from triviador.domain.ids import MediaAssetId, PlayerId, SessionId, UserId
from triviador.services.identity import AuthenticatedPrincipal, UserRole

FORBIDDEN_FIELDS = {"is_correct", "correct", "correct_index", "correct_choice_index",
                    "correct_choice_id", "correct_value", "numeric_answer", "answer"}


def test_the_question_model_declares_no_answer_field_anywhere() -> None:
    """Walks the models, not an instance: a field that only appears on a
    numeric question would pass an instance check made against an MC
    fixture, and vice versa."""
    from triviador.api.schemas.games import ClientChoice

    names = set(ClientQuestion.model_fields) | set(ClientChoice.model_fields)
    assert not (names & FORBIDDEN_FIELDS), sorted(names & FORBIDDEN_FIELDS)


def test_a_multiple_choice_question_projects_its_text_and_never_its_key() -> None:
    projected = project_question(mc_question(1, correct=2), media_base="/media")
    assert [c.text for c in projected.choices or ()] == ["a", "b", "c", "d"]
    assert "correct" not in projected.model_dump_json()


def test_a_numeric_question_projects_without_its_value() -> None:
    projected = project_question(numeric_question(1, answer=42), media_base="/media")
    assert projected.choices is None
    assert "42" not in projected.model_dump_json()


def test_the_revealed_answer_is_a_separate_type_that_does_carry_it() -> None:
    """The withholding is structural, so revealing must be too: a different
    model, constructed only by `QuestionResolved`'s projection (Task 12)."""
    revealed = RevealedAnswer.of(numeric_question(1, answer=42))
    assert revealed.correct_value == Decimal(42)
    assert RevealedAnswer.of(mc_question(1, correct=2)).correct_choice_index == 2


def test_media_is_an_opaque_content_addressed_url() -> None:
    """§9.6: prefetching ~29 of these must leak neither prompt nor answer,
    which is exactly what a content-addressed id gives — and why the URL is
    built from the asset id alone, never from the question id or its text."""
    from dataclasses import replace

    question = replace(numeric_question(1, answer=42), media_asset_id=MediaAssetId("a3f9c1"))
    projected = project_question(question, media_base="/media")
    assert projected.media_url == "/media/a3f9c1"


def test_a_question_without_media_has_no_url() -> None:
    assert project_question(numeric_question(1, 42), media_base="/media").media_url is None


def test_the_viewer_is_a_participant_only_when_the_state_says_so() -> None:
    state = lobby_state({"p1": 0, "p2": 1})
    inside = viewer_for(state, AuthenticatedPrincipal(UserId("p1"), UserRole.PLAYER,
                                                     SessionId("s")))
    outside = viewer_for(state, AuthenticatedPrincipal(UserId("p9"), UserRole.PLAYER,
                                                       SessionId("s")))
    assert inside.player_id == PlayerId("p1")
    assert outside.player_id is None


def test_an_admin_who_is_not_playing_is_still_not_a_participant() -> None:
    """Role and participation are different questions. Spec 2 adds
    spectating; until then an admin's standing in a game they are not in is
    the same as anyone else's."""
    state = lobby_state({"p1": 0})
    viewer = viewer_for(state, AuthenticatedPrincipal(UserId("admin"), UserRole.ADMIN,
                                                      SessionId("s")))
    assert viewer.player_id is None
    assert viewer.role is UserRole.ADMIN
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd backend && uv run pytest tests/api/test_projection_question.py -v --no-cov`
Expected: FAIL — `triviador.api.projection.viewer` does not exist.

- [ ] **Step 3: Write the viewer**

`backend/src/triviador/api/projection/viewer.py`:

```python
"""Who is looking, from the point of view of one game.

Spec 1B §6.5: a connection stores an `AuthenticatedPrincipal`, and a
`ViewerContext` is constructed per `(connection, game)` after membership
authorization. Keeping them separate types is what stops "authenticated"
from being mistaken for "a player in this game".
"""

from dataclasses import dataclass

from triviador.domain.game.state import GameState
from triviador.domain.ids import PlayerId, UserId
from triviador.services.identity import AuthenticatedPrincipal, UserRole


@dataclass(frozen=True)
class ViewerContext:
    user_id: UserId
    player_id: PlayerId | None
    role: UserRole


def viewer_for(state: GameState, principal: AuthenticatedPrincipal) -> ViewerContext:
    """`player_id` is a membership test, never a lookup.

    A user's `PlayerId` in a game *is* their `UserId` — `game_players.
    user_id` is a foreign key to `users.id` — so participation is exactly
    "is this id among the seated players", and there is no table that could
    disagree with the folded state.
    """
    player_id = PlayerId(principal.user_id)
    return ViewerContext(
        user_id=principal.user_id,
        player_id=player_id if player_id in state.players else None,
        role=principal.role,
    )
```

- [ ] **Step 4: Write the question DTOs**

Start `backend/src/triviador/api/schemas/games.py`:

```python
"""The player-facing shapes. Nothing here shares a base class with a
domain event or a domain state (§8.7) — these are Pydantic models over
plain JSON types, and the domain is frozen dataclasses over `Decimal`,
`NewType` and `Mapping`. The gap is deliberate: it is what makes
`send_json(event.model_dump())` fail to typecheck.
"""

from decimal import Decimal

from pydantic import BaseModel, ConfigDict

from triviador.domain.questions.types import Difficulty, QuestionKind, QuestionSnapshot


def media_url(media_base: str, asset_id: str | None) -> str | None:
    return None if asset_id is None else f"{media_base}/{asset_id}"


class ClientChoice(BaseModel):
    """No `is_correct`. §12.3 rejects byte-scanning as the test for this,
    because the correct answer's *text* is legitimate content — so the
    guarantee has to be that the flag has nowhere to live."""

    model_config = ConfigDict(extra="forbid")

    idx: int
    text: str
    media_url: str | None = None


class ClientQuestion(BaseModel):
    model_config = ConfigDict(extra="forbid")

    question_id: str
    kind: QuestionKind
    prompt: str
    category: str
    difficulty: Difficulty
    choices: tuple[ClientChoice, ...] | None
    unit: str | None
    media_url: str | None


class RevealedAnswer(BaseModel):
    """The other half, constructed only by `QuestionResolved`'s projection.

    A separate model rather than optional fields on `ClientQuestion`: an
    optional field is one `exclude_none=False` away from being emitted, and
    the whole point is that before resolution there is no field at all.
    """

    model_config = ConfigDict(extra="forbid")

    correct_choice_index: int | None
    correct_value: Decimal | None

    @classmethod
    def of(cls, question: QuestionSnapshot) -> "RevealedAnswer":
        return cls(
            correct_choice_index=(
                question.correct_choice_index() if question.choices is not None else None
            ),
            correct_value=question.numeric_answer,
        )


def project_question(question: QuestionSnapshot, *, media_base: str) -> ClientQuestion:
    return ClientQuestion(
        question_id=str(question.question_id),
        kind=question.kind,
        prompt=question.prompt,
        category=question.category.name,
        difficulty=question.difficulty,
        choices=(
            None
            if question.choices is None
            else tuple(
                ClientChoice(
                    idx=c.idx,
                    text=c.text,
                    media_url=media_url(media_base, c.media_asset_id),
                )
                for c in question.choices
            )
        ),
        unit=question.unit,
        media_url=media_url(media_base, question.media_asset_id),
    )
```

- [ ] **Step 5: Run the tests, and the projection layering gate**

Run: `cd backend && uv run pytest tests/api/test_projection_question.py tests/test_layering.py -v --no-cov && uv run mypy --strict`
Expected: PASS. The gate now has real files to walk: `viewer.py` imports `domain` and `services.identity` only.

`schemas/games.py` lives outside `api/projection/` and is therefore not gated — deliberately, because Tasks 10–11 will have it import nothing heavier either, and the models must stay importable from `api/http/` and `api/ws/` alike.

- [ ] **Step 6: Commit**

```bash
git add backend/src/triviador/api backend/tests/api
git commit -m "feat(api): the viewer, and a question DTO with nowhere to put the answer"
```

---

## Task 10: The turn, and the affordances that keep the ruleset on the server

**Files:**
- Modify: `backend/src/triviador/api/schemas/games.py`
- Create: `backend/src/triviador/api/projection/turns.py`
- Test: `backend/tests/api/test_projection_turns.py`

**Interfaces:**
- Consumes: `Turn` and its eight variants (`domain/game/state.py`); `legal_targets` (`domain/game/reducer.py`); `GameState.free_regions`; `ViewerContext`; `project_question`.
- Produces: `ClientTurn` (a discriminated union on `kind` of `WarmupTurn`, `QuestionTurn`, `PickingTurn`, `TargetSelectTurn`, `DuelTurn`, `NeutralTurn`, `FinalTurn`); `YourOptions(pick, attack)`; `project_turn(state, viewer, media_base) -> ClientTurn | None`.

- [ ] **Step 1: Write the failing test**

`backend/tests/api/test_projection_turns.py`:

```python
"""§8.8: the projection carries affordances, not just facts.

The client greys out illegal moves by highlighting exactly `your_options`.
It does not derive them — deriving them means shipping adjacency and
ownership rules to the browser, i.e. a second copy of the ruleset that can
disagree with `domain/maps`.
"""

from datetime import timedelta

import pytest

from tests.conftest import NOW, a_player, full_pool, grid_map, lobby_state, own
from triviador.api.projection.turns import project_turn
from triviador.api.projection.viewer import ViewerContext
from triviador.domain.game.reducer import legal_targets
from triviador.domain.game.state import (
    BattleTargetSelect,
    Deadline,
    DeadlineKind,
    ExpansionPicking,
    ExpansionQuestion,
    MediaWarmup,
    Phase,
)
from triviador.domain.ids import DeadlineId, PlayerId, RegionId, UserId
from triviador.services.identity import UserRole

MEDIA = "/media"


def viewer(pid: str | None) -> ViewerContext:
    return ViewerContext(
        UserId(pid or "watcher"), PlayerId(pid) if pid else None, UserRole.PLAYER
    )


def deadline(kind: DeadlineKind) -> Deadline:
    return Deadline(DeadlineId(7), kind, NOW + timedelta(seconds=20))


def test_no_turn_projects_to_none() -> None:
    assert project_turn(lobby_state(), viewer("p1"), media_base=MEDIA) is None


def test_the_warmup_turn_carries_only_its_deadline() -> None:
    from dataclasses import replace

    state = replace(lobby_state(), turn=MediaWarmup(deadline(DeadlineKind.WARMUP)))
    turn = project_turn(state, viewer("p1"), media_base=MEDIA)
    assert turn is not None and turn.kind == "media_warmup"
    assert turn.deadline_id == 7
    assert turn.your_options.pick == () and turn.your_options.attack == ()


def question_state():
    from dataclasses import replace

    return replace(
        lobby_state(),
        phase=Phase.EXPANSION,
        pool=full_pool(),
        turn=ExpansionQuestion(
            deadline=deadline(DeadlineKind.ANSWER),
            question=full_pool().numeric[0],
            answers={},
        ),
    )


def test_a_question_turn_names_who_has_answered_and_never_what_they_said() -> None:
    """§8.7's middle row: to a participant, `AnswerSubmitted` is the fact,
    not the value. The snapshot has to say the same thing, or a reconnect
    reveals what the live stream withheld."""
    from dataclasses import replace

    from triviador.domain.game.state import NumericAnswer, SubmittedAnswer
    from decimal import Decimal

    base = question_state()
    assert isinstance(base.turn, ExpansionQuestion)
    state = replace(
        base,
        turn=replace(
            base.turn,
            answers={PlayerId("p2"): SubmittedAnswer(NumericAnswer(Decimal(99)), 1200)},
        ),
    )
    turn = project_turn(state, viewer("p1"), media_base=MEDIA)
    assert turn is not None and turn.kind == "expansion_question"
    assert turn.answered == ("p2",)
    assert turn.your_answer is None
    assert "99" not in turn.model_dump_json()


def test_an_author_sees_their_own_answer_back() -> None:
    """§8.7's right-hand column. Without it a reconnect mid-window loses
    what the player already typed, and they retype it into a window that
    will reject the change as ALREADY_ANSWERED."""
    from dataclasses import replace
    from decimal import Decimal

    from triviador.domain.game.state import NumericAnswer, SubmittedAnswer

    base = question_state()
    assert isinstance(base.turn, ExpansionQuestion)
    state = replace(
        base,
        turn=replace(
            base.turn,
            answers={PlayerId("p1"): SubmittedAnswer(NumericAnswer(Decimal(99)), 1200)},
        ),
    )
    turn = project_turn(state, viewer("p1"), media_base=MEDIA)
    assert turn is not None and turn.your_answer is not None
    assert turn.your_answer.value == "99"


def picking_state(current: str):
    from dataclasses import replace

    state = own(lobby_state(), "r0", "p1")
    return replace(
        state,
        phase=Phase.EXPANSION,
        turn=ExpansionPicking(
            deadline=deadline(DeadlineKind.PICK),
            pick_order=(PlayerId("p1"), PlayerId("p2"), PlayerId("p3")),
            grants_remaining={PlayerId("p1"): 2, PlayerId("p2"): 1, PlayerId("p3"): 0},
            current_picker=PlayerId(current),
        ),
    )


def test_the_current_picker_is_offered_exactly_the_free_regions() -> None:
    state = picking_state("p1")
    turn = project_turn(state, viewer("p1"), media_base=MEDIA)
    assert turn is not None and turn.kind == "expansion_picking"
    assert set(turn.your_options.pick) == set(state.free_regions())
    assert RegionId("r0") not in turn.your_options.pick


def test_a_player_who_is_not_picking_is_offered_nothing() -> None:
    """The affordance is per viewer. A shared list would let the client
    render a legal-looking move for the wrong player, and the server would
    then reject it — which reads as a bug in the game, not in the client."""
    turn = project_turn(picking_state("p2"), viewer("p1"), media_base=MEDIA)
    assert turn is not None and turn.your_options.pick == ()


def test_a_non_participant_is_offered_nothing() -> None:
    turn = project_turn(picking_state("p1"), viewer(None), media_base=MEDIA)
    assert turn is not None and turn.your_options.pick == ()


def target_state():
    from dataclasses import replace

    state = own(own(lobby_state(), "r0", "p1"), "r4", "p2")
    return replace(
        state,
        phase=Phase.BATTLE,
        turn=BattleTargetSelect(
            deadline=deadline(DeadlineKind.TARGET_SELECT), attacker_id=PlayerId("p1")
        ),
    )


def test_the_attacker_is_offered_exactly_legal_targets() -> None:
    """The one source of the adjacency rule is `legal_targets`, which the
    reducer's own guard 6 also calls. Recomputing the set here — even
    correctly — would be a second copy that can drift."""
    state = target_state()
    turn = project_turn(state, viewer("p1"), media_base=MEDIA)
    assert turn is not None and turn.kind == "battle_target_select"
    assert set(turn.your_options.attack) == set(legal_targets(state, PlayerId("p1")))
    assert turn.your_options.pick == ()


def test_the_defender_is_offered_nothing_during_a_target_selection() -> None:
    turn = project_turn(target_state(), viewer("p2"), media_base=MEDIA)
    assert turn is not None and turn.your_options.attack == ()


def test_every_turn_variant_has_a_projection() -> None:
    """An unmapped `Turn` variant must be a `mypy --strict` error at the
    `assert_never`, not a `None` the client renders as an empty board. This
    test is the runtime half: it enumerates the union and asserts each name
    appears in the projected `kind` literal set."""
    from typing import get_args

    from triviador.api.schemas.games import ClientTurn
    from triviador.domain.game.state import Turn

    # Two unwraps: `ClientTurn` is `Annotated[Union, Field(...)]`, so
    # `get_args` returns `(Union, Field)` and the union's own members need a
    # second call. One unwrap raises `AttributeError` at collection time.
    variants = get_args(get_args(ClientTurn)[0])
    kinds = {get_args(v.model_fields["kind"].annotation)[0] for v in variants}
    assert len(kinds) == len(get_args(Turn)) - 1  # BattleTiebreak shares DuelTurn's shape
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd backend && uv run pytest tests/api/test_projection_turns.py -v --no-cov`
Expected: FAIL — `triviador.api.projection.turns` does not exist.

- [ ] **Step 3: Add the turn models**

Append to `backend/src/triviador/api/schemas/games.py`:

```python
class SubmittedValue(BaseModel):
    """A player's own answer, echoed back to its author only.

    `value` is a string even for a numeric answer: JSON has one number type
    and it is a float, so `Decimal("0.1")` round-trips through it wrong.
    Every numeric value on this API is a decimal string for that reason.
    """

    model_config = ConfigDict(extra="forbid")

    kind: Literal["choice", "numeric"]
    idx: int | None = None
    value: str | None = None


class YourOptions(BaseModel):
    """§8.8's `your_options`, per viewer. Both lists empty is the normal
    case — it is not this viewer's move."""

    model_config = ConfigDict(extra="forbid")

    pick: tuple[str, ...] = ()
    attack: tuple[str, ...] = ()


class _TurnBase(BaseModel):
    model_config = ConfigDict(extra="forbid")

    deadline_id: int
    deadline_at: datetime
    your_options: YourOptions = YourOptions()


class WarmupTurn(_TurnBase):
    kind: Literal["media_warmup"] = "media_warmup"


class QuestionTurn(_TurnBase):
    kind: Literal["expansion_question"] = "expansion_question"
    question: ClientQuestion
    answered: tuple[str, ...]
    your_answer: SubmittedValue | None = None


class PickingTurn(_TurnBase):
    kind: Literal["expansion_picking"] = "expansion_picking"
    pick_order: tuple[str, ...]
    grants_remaining: dict[str, int]
    current_picker: str


class TargetSelectTurn(_TurnBase):
    kind: Literal["battle_target_select"] = "battle_target_select"
    attacker_id: str


class DuelTurn(_TurnBase):
    """`BattleDuel` and `BattleTiebreak` share this shape; `tiebreak`
    distinguishes them. Two models would be two identical field lists and
    two Zod schemas for one screen."""

    kind: Literal["battle_duel"] = "battle_duel"
    tiebreak: bool
    attacker_id: str
    defender_id: str
    region_id: str
    question: ClientQuestion
    answered: tuple[str, ...]
    your_answer: SubmittedValue | None = None


class NeutralTurn(_TurnBase):
    kind: Literal["neutral_challenge"] = "neutral_challenge"
    attacker_id: str
    region_id: str
    question: ClientQuestion
    answered: tuple[str, ...]
    your_answer: SubmittedValue | None = None


class FinalTurn(_TurnBase):
    kind: Literal["final_tiebreak"] = "final_tiebreak"
    contenders: tuple[str, ...]
    question: ClientQuestion
    answered: tuple[str, ...]
    your_answer: SubmittedValue | None = None


ClientTurn = Annotated[
    WarmupTurn | QuestionTurn | PickingTurn | TargetSelectTurn | DuelTurn | NeutralTurn | FinalTurn,
    Field(discriminator="kind"),
]
```

Add `from datetime import datetime`, `from typing import Annotated, Literal`, `from pydantic import Field`.

- [ ] **Step 4: Write the turn projection**

`backend/src/triviador/api/projection/turns.py`:

```python
"""`Turn` → `ClientTurn`, with the viewer's own affordances attached.

Every option list here is *read from the domain*, never recomputed:
`state.free_regions()` and `legal_targets(state, player)` are the same
functions the reducer's guards call. That is the whole property §8.8 buys —
the client highlights exactly what the server would accept, and adjacency
lives in `domain/maps` alone.
"""

from typing import assert_never

from triviador.api.projection.viewer import ViewerContext
from triviador.api.schemas.games import (
    ClientTurn,
    DuelTurn,
    FinalTurn,
    NeutralTurn,
    PickingTurn,
    QuestionTurn,
    SubmittedValue,
    TargetSelectTurn,
    WarmupTurn,
    YourOptions,
    project_question,
)
from triviador.domain.game.reducer import legal_targets
from triviador.domain.game.state import (
    BattleDuel,
    BattleTargetSelect,
    BattleTiebreak,
    ChoiceAnswer,
    ExpansionPicking,
    ExpansionQuestion,
    FinalTiebreak,
    GameState,
    MediaWarmup,
    NeutralChallenge,
    NumericAnswer,
    SubmittedAnswer,
    Turn,
)


def _answered(turn: object) -> tuple[str, ...]:
    answers = getattr(turn, "answers", {})
    return tuple(str(p) for p in answers)


def _own_answer(turn: Turn, viewer: ViewerContext) -> SubmittedValue | None:
    answers = getattr(turn, "answers", {})
    if viewer.player_id is None:
        return None
    submitted: SubmittedAnswer | None = answers.get(viewer.player_id)
    if submitted is None:
        return None
    if isinstance(submitted.value, ChoiceAnswer):
        return SubmittedValue(kind="choice", idx=submitted.value.idx)
    return SubmittedValue(kind="numeric", value=str(submitted.value.value))


def project_turn(state: GameState, viewer: ViewerContext, *, media_base: str) -> ClientTurn | None:
    turn = state.turn
    if turn is None:
        return None

    common = {"deadline_id": int(turn.deadline.id), "deadline_at": turn.deadline.deadline_at}
    me = viewer.player_id

    match turn:
        case MediaWarmup():
            return WarmupTurn(**common)
        case ExpansionQuestion():
            return QuestionTurn(
                **common,
                question=project_question(turn.question, media_base=media_base),
                answered=_answered(turn),
                your_answer=_own_answer(turn, viewer),
            )
        case ExpansionPicking():
            options = (
                YourOptions(pick=tuple(str(r) for r in state.free_regions()))
                if me is not None and me == turn.current_picker
                else YourOptions()
            )
            return PickingTurn(
                **common,
                your_options=options,
                pick_order=tuple(str(p) for p in turn.pick_order),
                grants_remaining={str(p): n for p, n in turn.grants_remaining.items()},
                current_picker=str(turn.current_picker),
            )
        case BattleTargetSelect():
            options = (
                YourOptions(attack=tuple(str(r) for r in legal_targets(state, me)))
                if me is not None and me == turn.attacker_id
                else YourOptions()
            )
            return TargetSelectTurn(
                **common, your_options=options, attacker_id=str(turn.attacker_id)
            )
        case BattleDuel() | BattleTiebreak():
            return DuelTurn(
                **common,
                tiebreak=isinstance(turn, BattleTiebreak),
                attacker_id=str(turn.attacker_id),
                defender_id=str(turn.defender_id),
                region_id=str(turn.region_id),
                question=project_question(turn.question, media_base=media_base),
                answered=_answered(turn),
                your_answer=_own_answer(turn, viewer),
            )
        case NeutralChallenge():
            return NeutralTurn(
                **common,
                attacker_id=str(turn.attacker_id),
                region_id=str(turn.region_id),
                question=project_question(turn.question, media_base=media_base),
                answered=_answered(turn),
                your_answer=_own_answer(turn, viewer),
            )
        case FinalTiebreak():
            return FinalTurn(
                **common,
                contenders=tuple(str(p) for p in turn.contenders),
                question=project_question(turn.question, media_base=media_base),
                answered=_answered(turn),
                your_answer=_own_answer(turn, viewer),
            )
        case _:
            assert_never(turn)
```

The `assert_never` is the important line: adding a ninth `Turn` variant in a later spec fails `mypy --strict` here rather than silently projecting `None`, which the client would render as a board with no move to make and no timer.

- [ ] **Step 5: Run the tests**

Run: `cd backend && uv run pytest tests/api/test_projection_turns.py -v --no-cov && uv run mypy --strict`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add backend/src/triviador/api backend/tests/api
git commit -m "feat(api): per-viewer turn projection with server-computed affordances"
```

---

## Task 11: The snapshot, and the pool it must never contain

**Files:**
- Modify: `backend/src/triviador/api/schemas/games.py`
- Create: `backend/src/triviador/api/projection/snapshot.py`
- Test: `backend/tests/api/test_projection_snapshot.py`

**Interfaces:**
- Consumes: `GameState`, `ViewerContext`, `project_turn`, `GameRules`.
- Produces: `ClientPlayer`, `ClientTerritory`, `ClientRules`, `ClientYou`, `ClientGameState`, `GameSnapshot(seq, state)`; `project_snapshot(state, viewer, media_base) -> GameSnapshot`.

- [ ] **Step 1: Write the failing test**

`backend/tests/api/test_projection_snapshot.py`:

```python
"""§8.7's `project_snapshot`, and the one leak that would end the game.

`GameState.pool` holds every question of the whole match, each with its
`is_correct` flags. It is the single largest secret in the system and it
sits one attribute away from the object being projected.
"""

from dataclasses import replace
from datetime import timedelta

from tests.conftest import NOW, full_pool, lobby_state, own
from triviador.api.projection.snapshot import project_snapshot
from triviador.api.projection.viewer import ViewerContext
from triviador.domain.game.rules import DEFAULT_RULES
from triviador.domain.game.state import (
    Deadline,
    DeadlineKind,
    ExpansionQuestion,
    Phase,
    TerritoryKind,
)
from triviador.domain.ids import DeadlineId, MediaAssetId, PlayerId, RegionId, UserId
from triviador.services.identity import UserRole

MEDIA = "/media"


def viewer(pid: str | None = "p1") -> ViewerContext:
    return ViewerContext(UserId(pid or "x"), PlayerId(pid) if pid else None, UserRole.PLAYER)


def playing_state():
    pool = full_pool()
    state = own(own(lobby_state(), "r0", "p1"), "r4", "p2")
    return replace(
        state,
        seq=42,
        phase=Phase.EXPANSION,
        round_no=2,
        pool=pool,
        turn=ExpansionQuestion(
            deadline=Deadline(DeadlineId(3), DeadlineKind.ANSWER, NOW + timedelta(seconds=20)),
            question=pool.numeric[0],
            answers={},
        ),
    )


def test_the_snapshot_carries_the_sequence_it_was_taken_at() -> None:
    """§8.4's dispatcher compares `seq`, and §9.3's cache writer keeps the
    newer of a REST response and a WS update by comparing it. It lives on
    the envelope, not inside the state, so there is one of it."""
    snapshot = project_snapshot(playing_state(), viewer(), media_base=MEDIA)
    assert snapshot.seq == 42
    assert "seq" not in snapshot.state.model_fields


def test_not_one_undrawn_question_reaches_the_client() -> None:
    """The whole match's pool is in `state.pool`. Exactly one question — the
    one the open turn presents — may appear, and only through the turn."""
    state = playing_state()
    body = project_snapshot(state, viewer(), media_base=MEDIA).model_dump_json()
    assert state.pool.numeric[0].prompt in body
    for question in (*state.pool.numeric[1:], *state.pool.multiple_choice):
        assert question.prompt not in body


def test_no_correct_answer_of_any_kind_appears() -> None:
    state = playing_state()
    body = project_snapshot(state, viewer(), media_base=MEDIA).model_dump_json()
    assert "is_correct" not in body
    assert "numeric_answer" not in body
    assert str(state.pool.numeric[0].numeric_answer) not in body


def test_media_prefetch_covers_the_pool_and_is_opaque() -> None:
    """§9.6: the client must prefetch every image before any timer starts,
    which means it needs URLs for questions it has not been shown. They are
    content-addressed ids and nothing else — no prompt, no question id, no
    ordering that maps back to the pool."""
    pool = full_pool(numeric=2, mc=0)
    with_media = replace(
        pool, numeric=tuple(replace(q, media_asset_id=MediaAssetId(f"asset{i}"))
                            for i, q in enumerate(pool.numeric))
    )
    state = replace(playing_state(), pool=with_media)
    snapshot = project_snapshot(state, viewer(), media_base=MEDIA)
    assert set(snapshot.state.media_prefetch) == {"/media/asset0", "/media/asset1"}
    for url in snapshot.state.media_prefetch:
        assert "numeric" not in url


def test_a_lobby_projects_with_no_turn_and_nothing_to_prefetch() -> None:
    snapshot = project_snapshot(lobby_state(), viewer(), media_base=MEDIA)
    assert snapshot.state.turn is None
    assert snapshot.state.media_prefetch == ()
    assert snapshot.state.phase == Phase.LOBBY


def test_every_player_and_every_region_is_present() -> None:
    """The client renders the whole board from this and nothing else, so a
    partial projection is a partial board."""
    state = playing_state()
    snapshot = project_snapshot(state, viewer(), media_base=MEDIA)
    assert {p.player_id for p in snapshot.state.players} == {str(p) for p in state.players}
    assert {t.region_id for t in snapshot.state.territories} == {
        str(r) for r in state.map.region_ids()
    }


def test_territory_ownership_and_bases_survive_the_projection() -> None:
    state = playing_state()
    projected = {t.region_id: t for t in project_snapshot(state, viewer(),
                                                          media_base=MEDIA).state.territories}
    assert projected["r0"].owner_id == "p1"
    assert projected["r1"].owner_id is None
    assert projected["r0"].kind == TerritoryKind.NORMAL


def test_the_you_block_tells_a_participant_who_they_are() -> None:
    snapshot = project_snapshot(playing_state(), viewer("p1"), media_base=MEDIA)
    assert snapshot.state.you.player_id == "p1"
    assert snapshot.state.you.role is UserRole.PLAYER


def test_a_non_participant_gets_a_you_block_with_no_player() -> None:
    snapshot = project_snapshot(playing_state(), viewer(None), media_base=MEDIA)
    assert snapshot.state.you.player_id is None


def test_the_rules_are_public_and_projected_whole() -> None:
    """Nothing in `GameRules` is secret — it is frozen at creation and the
    client needs it to say "round 2 of 4" — so withholding any of it would
    only mean the client guessing."""
    snapshot = project_snapshot(playing_state(), viewer(), media_base=MEDIA)
    assert snapshot.state.rules.expansion_rounds == DEFAULT_RULES.expansion_rounds
    assert snapshot.state.rules.claims_by_rank == DEFAULT_RULES.claims_by_rank


def test_adjacency_is_never_projected() -> None:
    """§6.1 says `GET /api/maps/{id}` never returns adjacency; the snapshot
    is the other door to the same fact, and §8.8 is the reason — the client
    is told its options, not the rule that produced them."""
    body = project_snapshot(playing_state(), viewer(), media_base=MEDIA).model_dump_json()
    assert "adjacency" not in body
    assert "neighbours" not in body
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd backend && uv run pytest tests/api/test_projection_snapshot.py -v --no-cov`
Expected: FAIL — `triviador.api.projection.snapshot` does not exist.

- [ ] **Step 3: Add the state models**

Append to `backend/src/triviador/api/schemas/games.py`:

```python
class ClientPlayer(BaseModel):
    model_config = ConfigDict(extra="forbid")

    player_id: str
    display_name: str
    seat: int
    score: int
    bonus_score: int
    base_region: str | None
    is_eliminated: bool


class ClientTerritory(BaseModel):
    model_config = ConfigDict(extra="forbid")

    region_id: str
    owner_id: str | None
    kind: TerritoryKind
    base_owner_id: str | None
    base_hp: int | None
    acquisition: AcquisitionKind | None


class ClientRules(BaseModel):
    """`GameRules`, verbatim. Public by construction: it is frozen into the
    `GameCreated` event at creation and every player is playing under it."""

    model_config = ConfigDict(extra="forbid")

    player_count: int
    expansion_rounds: int
    battle_rounds: int
    base_hp: int
    answer_timeout_ms: int
    pick_timeout_ms: int
    warmup_ms: int
    claims_by_rank: tuple[int, ...]
    pts_base: int
    pts_territory: int
    pts_conquered: int
    pts_defense: int


class ClientYou(BaseModel):
    """Who the recipient is *in this game*. Present so the client never has
    to correlate its `/api/auth/me` id against the player list itself and
    get it wrong for a spectating admin."""

    model_config = ConfigDict(extra="forbid")

    player_id: str | None
    role: UserRole


class ClientGameState(BaseModel):
    model_config = ConfigDict(extra="forbid")

    game_id: str
    map_id: str
    phase: Phase
    round_no: int
    rules: ClientRules
    turn_order: tuple[str, ...]
    players: tuple[ClientPlayer, ...]
    territories: tuple[ClientTerritory, ...]
    turn: ClientTurn | None
    winner_id: str | None
    media_prefetch: tuple[str, ...]
    you: ClientYou


class GameSnapshot(BaseModel):
    """One projection, two transports (§9.3): the body of
    `GET /api/games/{id}` and the payload of `game.snapshot`."""

    model_config = ConfigDict(extra="forbid")

    seq: int
    state: ClientGameState
```

Add the imports: `from triviador.domain.game.state import AcquisitionKind, Phase, TerritoryKind` and `from triviador.services.identity import UserRole`.

- [ ] **Step 4: Write the snapshot projection**

`backend/src/triviador/api/projection/snapshot.py`:

```python
"""`GameState` → `GameSnapshot`, per viewer.

Field by field, deliberately: a `model_validate(asdict(state))` would
project `pool` — every question of the match with its `is_correct` flags —
the first time anyone added a field to `GameState`, and no test that did not
already know to look for it would notice.
"""

from triviador.api.projection.turns import project_turn
from triviador.api.projection.viewer import ViewerContext
from triviador.api.schemas.games import (
    ClientGameState,
    ClientPlayer,
    ClientRules,
    ClientTerritory,
    ClientYou,
    GameSnapshot,
)
from triviador.domain.game.rules import GameRules
from triviador.domain.game.state import GameState
from triviador.domain.questions.types import QuestionPool


def _rules(rules: GameRules) -> ClientRules:
    return ClientRules(
        player_count=rules.player_count,
        expansion_rounds=rules.expansion_rounds,
        battle_rounds=rules.battle_rounds,
        base_hp=rules.base_hp,
        answer_timeout_ms=rules.answer_timeout_ms,
        pick_timeout_ms=rules.pick_timeout_ms,
        warmup_ms=rules.warmup_ms,
        claims_by_rank=rules.claims_by_rank,
        pts_base=rules.pts_base,
        pts_territory=rules.pts_territory,
        pts_conquered=rules.pts_conquered,
        pts_defense=rules.pts_defense,
    )


def _media_prefetch(pool: QuestionPool, media_base: str) -> tuple[str, ...]:
    """Every image the match can present, as opaque URLs (§9.6).

    This is the *only* thing derived from the pool, and it is safe for the
    exact reason §9.6 gives: a content-addressed id carries no prompt, no
    answer, and no ordering that maps back to which question is which.
    Sorted, so the list does not leak the pool's draw order either.
    """
    assets = {
        q.media_asset_id
        for q in (*pool.numeric, *pool.multiple_choice)
        if q.media_asset_id is not None
    }
    assets |= {
        c.media_asset_id
        for q in pool.multiple_choice
        for c in (q.choices or ())
        if c.media_asset_id is not None
    }
    return tuple(f"{media_base}/{a}" for a in sorted(assets))


def project_snapshot(
    state: GameState, viewer: ViewerContext, *, media_base: str
) -> GameSnapshot:
    return GameSnapshot(
        seq=state.seq,
        state=ClientGameState(
            game_id=str(state.game_id),
            map_id=str(state.map.map_id),
            phase=state.phase,
            round_no=state.round_no,
            rules=_rules(state.rules),
            turn_order=tuple(str(p) for p in state.turn_order),
            players=tuple(
                ClientPlayer(
                    player_id=str(p.player_id),
                    display_name=p.display_name,
                    seat=p.seat,
                    score=p.score,
                    bonus_score=p.bonus_score,
                    base_region=None if p.base_region is None else str(p.base_region),
                    is_eliminated=p.is_eliminated,
                )
                for p in sorted(state.players.values(), key=lambda p: p.seat)
            ),
            territories=tuple(
                ClientTerritory(
                    region_id=str(region_id),
                    owner_id=None if t.owner_id is None else str(t.owner_id),
                    kind=t.kind,
                    base_owner_id=None if t.base_owner_id is None else str(t.base_owner_id),
                    base_hp=t.base_hp,
                    acquisition=t.acquisition,
                )
                for region_id in state.map.region_ids()
                for t in (state.territories[region_id],)
            ),
            turn=project_turn(state, viewer, media_base=media_base),
            winner_id=None if state.winner_id is None else str(state.winner_id),
            media_prefetch=_media_prefetch(state.pool, media_base),
            you=ClientYou(
                player_id=None if viewer.player_id is None else str(viewer.player_id),
                role=viewer.role,
            ),
        ),
    )
```

Note `state.pending_attack`, `state.last_attacker_id`, `state.pending_final_contenders` and `state.next_deadline_id` are **not** projected: they are reducer bookkeeping, invisible in the UI, and `pending_attack` in particular would reveal a declared attack before the question that follows it is open.

- [ ] **Step 5: Run the tests**

Run: `cd backend && uv run pytest tests/api -v --no-cov && uv run mypy --strict`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add backend/src/triviador/api backend/tests/api
git commit -m "feat(api): the per-viewer snapshot, with the question pool sealed out"
```

---

## Task 12: Event projection — narration, not the log

**Files:**
- Create: `backend/src/triviador/api/schemas/events.py`, `backend/src/triviador/api/projection/events.py`
- Test: `backend/tests/api/test_projection_events.py`

**Interfaces:**
- Consumes: every member of `GameEvent`; `ViewerContext`; `RevealedAnswer`; `SubmittedValue`.
- Produces: `ClientEvent` (discriminated union on `type`); `project_event(event, viewer) -> ClientEvent | None`.

- [ ] **Step 1: Write the failing test**

`backend/tests/api/test_projection_events.py`:

```python
"""§8.7's table, and §8.4's reason a batch is the transport unit.

`project_event` may return `None`. That is not an oversight — it is why the
client is sequenced on the whole committed batch (`base_seq`/`seq`) rather
than per event: a client that saw 101 and 103 would conclude there was a
gap, resync, and repeat forever.
"""

from decimal import Decimal
from typing import get_args

import pytest

from tests.conftest import mc_question, numeric_question
from triviador.api.projection.events import project_event
from triviador.api.projection.viewer import ViewerContext
from triviador.domain.game import events as ev
from triviador.domain.game.events import GameEvent
from triviador.domain.game.rules import DEFAULT_RULES
from triviador.domain.game.state import Deadline, DeadlineKind, NumericAnswer, SubmittedAnswer
from triviador.domain.ids import DeadlineId, MapId, PlayerId, RegionId, UserId
from triviador.domain.questions.types import QuestionPool
from triviador.services.identity import UserRole


def viewer(pid: str | None = "p1") -> ViewerContext:
    return ViewerContext(UserId(pid or "x"), PlayerId(pid) if pid else None, UserRole.PLAYER)


def test_the_drawn_pool_never_reaches_a_client() -> None:
    """The single most dangerous event in the log: it carries the entire
    match's questions and their correct answers."""
    pool = QuestionPool(numeric=(numeric_question(1, 42),), multiple_choice=(mc_question(1),))
    assert project_event(ev.QuestionPoolDrawn(pool), viewer()) is None


def test_genesis_never_reaches_a_client() -> None:
    """`GameCreated` is consumed by `create_initial_state` and never folded,
    so it is never in a published batch; returning `None` makes that
    explicit rather than relying on it."""
    created = ev.GameCreated(MapId("grid"), DEFAULT_RULES, PlayerId("p1"), "sha")
    assert project_event(created, viewer()) is None


def test_an_answer_is_the_fact_to_everyone_else() -> None:
    """§8.7's middle row, the whole reason `publish` takes domain objects:
    one event, two different client events, decided per subscriber."""
    event = ev.AnswerSubmitted(PlayerId("p2"), SubmittedAnswer(NumericAnswer(Decimal(99)), 1200))
    projected = project_event(event, viewer("p1"))
    assert projected is not None and projected.type == "player_answered"
    assert projected.player_id == "p2"
    assert "99" not in projected.model_dump_json()


def test_an_answer_is_its_value_to_its_author() -> None:
    event = ev.AnswerSubmitted(PlayerId("p1"), SubmittedAnswer(NumericAnswer(Decimal(99)), 1200))
    projected = project_event(event, viewer("p1"))
    assert projected is not None and projected.your_answer is not None
    assert projected.your_answer.value == "99"


def test_the_elapsed_time_is_never_published() -> None:
    """It is the tiebreak key. Publishing it live would let a player time
    their own submission against an opponent's already-known speed."""
    event = ev.AnswerSubmitted(PlayerId("p1"), SubmittedAnswer(NumericAnswer(Decimal(99)), 1200))
    projected = project_event(event, viewer("p1"))
    assert projected is not None and "1200" not in projected.model_dump_json()


def test_resolution_reveals_everything_to_everyone() -> None:
    """§8.7's bottom row: after `QuestionResolved` the answer is public, and
    it is the same for a participant and its author."""
    event = ev.QuestionResolved(
        correct_choice_index=None,
        correct_value=Decimal(42),
        ranking=(PlayerId("p1"), PlayerId("p2")),
        correct_players=(PlayerId("p1"),),
    )
    for who in ("p1", "p2", None):
        projected = project_event(event, viewer(who))
        assert projected is not None and projected.type == "question_resolved"
        assert projected.correct_value == "42"
        assert projected.ranking == ("p1", "p2")


def test_a_presented_question_is_announced_without_being_repeated() -> None:
    """The question itself is in the snapshot's turn. Projecting it here as
    well would be a second place the withholding has to be right, and the
    second place is the one that gets it wrong."""
    deadline = Deadline(DeadlineId(4), DeadlineKind.ANSWER, __import__("tests.conftest",
                        fromlist=["NOW"]).NOW)
    projected = project_event(ev.QuestionPresented(numeric_question(1, 42), deadline), viewer())
    assert projected is not None and projected.type == "question_presented"
    assert projected.deadline_id == 4
    assert "42" not in projected.model_dump_json()
    assert "numeric 1?" not in projected.model_dump_json()


def test_every_domain_event_has_an_explicit_decision() -> None:
    """No event may fall through to a default. The failure this prevents is
    silent: a new event type added in a later spec would otherwise project
    as `None`, and the feature would simply never appear in any client with
    nothing anywhere reporting it."""
    from triviador.api.projection import events as module

    decided = module.PROJECTED | module.WITHHELD
    assert {t.__name__ for t in get_args(GameEvent)} == decided


@pytest.mark.parametrize("event_type", [t.__name__ for t in get_args(GameEvent)])
def test_no_projected_event_shares_a_base_class_with_its_domain_event(event_type: str) -> None:
    """§8.7: `DomainEvent` and `ServerMessage` are separate types with no
    shared base class, so `send_json(event.model_dump())` cannot compile."""
    from triviador.api.schemas import events as schemas

    for model in vars(schemas).values():
        if isinstance(model, type) and model.__module__ == schemas.__name__:
            assert not any(base.__module__.startswith("triviador.domain")
                           for base in model.__mro__[1:])
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd backend && uv run pytest tests/api/test_projection_events.py -v --no-cov`
Expected: FAIL — `triviador.api.projection.events` does not exist.

- [ ] **Step 3: Write the client event models**

`backend/src/triviador/api/schemas/events.py`. Every model is `extra="forbid"` and carries a `type` literal; they are grouped by what a player actually sees happen.

```python
"""What the client is *told happened*, as distinct from what is true.

Spec 1 §9.1: state is transported, events narrate. So these carry only
what an animation or a log line needs — never a field the snapshot already
holds authoritatively, and never a field §8.7 withholds.
"""

from decimal import Decimal
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

from triviador.api.schemas.games import SubmittedValue
from triviador.domain.game.events import ScoreReason
from triviador.domain.game.state import AcquisitionKind


class _Event(BaseModel):
    model_config = ConfigDict(extra="forbid")


class PlayerJoinedEvent(_Event):
    type: Literal["player_joined"] = "player_joined"
    player_id: str
    display_name: str
    seat: int


class PlayerLeftEvent(_Event):
    type: Literal["player_left"] = "player_left"
    player_id: str


class GameStartedEvent(_Event):
    type: Literal["game_started"] = "game_started"
    turn_order: tuple[str, ...]


class BasesAssignedEvent(_Event):
    type: Literal["bases_assigned"] = "bases_assigned"
    assignments: dict[str, str]


class WarmupStartedEvent(_Event):
    type: Literal["warmup_started"] = "warmup_started"
    deadline_id: int


class GameFinishedEvent(_Event):
    type: Literal["game_finished"] = "game_finished"
    winner_id: str | None
    final_scores: dict[str, int]


class GameAbortedEvent(_Event):
    type: Literal["game_aborted"] = "game_aborted"
    reason: str


class QuestionPresentedEvent(_Event):
    """The question itself is in the snapshot's turn; this is the cue."""

    type: Literal["question_presented"] = "question_presented"
    deadline_id: int


class PlayerAnsweredEvent(_Event):
    """§8.7: the fact to everyone, the value to its author only."""

    type: Literal["player_answered"] = "player_answered"
    player_id: str
    your_answer: SubmittedValue | None = None


class QuestionResolvedEvent(_Event):
    """`correct_value` is a decimal string for the reason every number on
    this API is: JSON's only number type is a float."""

    type: Literal["question_resolved"] = "question_resolved"
    correct_choice_index: int | None
    correct_value: str | None
    ranking: tuple[str, ...]
    correct_players: tuple[str, ...]


class RoundEvent(_Event):
    type: Literal["round_started", "round_completed"]
    phase: Literal["expansion", "battle"]
    round_no: int


class PicksGrantedEvent(_Event):
    type: Literal["picks_granted"] = "picks_granted"
    pick_order: tuple[str, ...]
    grants: dict[str, int]
    deadline_id: int


class TerritoryClaimedEvent(_Event):
    type: Literal["territory_claimed"] = "territory_claimed"
    player_id: str
    region_id: str
    acquisition: AcquisitionKind
    automatic: bool


class TurnStartedEvent(_Event):
    type: Literal["turn_started"] = "turn_started"
    attacker_id: str
    deadline_id: int


class TurnEndedEvent(_Event):
    type: Literal["turn_skipped", "turn_aborted"]
    attacker_id: str | None
    reason: str


class AttackDeclaredEvent(_Event):
    type: Literal["attack_declared"] = "attack_declared"
    attacker_id: str
    defender_id: str | None
    region_id: str


class DuelResolvedEvent(_Event):
    type: Literal["duel_resolved"] = "duel_resolved"
    winner_id: str | None


class TiebreakStartedEvent(_Event):
    type: Literal["tiebreak_started"] = "tiebreak_started"
    region_id: str


class TerritoryCapturedEvent(_Event):
    type: Literal["territory_captured"] = "territory_captured"
    region_id: str
    from_player_id: str | None
    to_player_id: str
    acquisition: AcquisitionKind


class NeutralCapturedEvent(_Event):
    type: Literal["neutral_captured"] = "neutral_captured"
    region_id: str
    player_id: str


class NeutralAttackFailedEvent(_Event):
    type: Literal["neutral_attack_failed"] = "neutral_attack_failed"
    region_id: str
    attacker_id: str


class DefenseHeldEvent(_Event):
    type: Literal["defense_held"] = "defense_held"
    region_id: str
    defender_id: str


class BaseDamagedEvent(_Event):
    type: Literal["base_damaged"] = "base_damaged"
    region_id: str
    hp_remaining: int


class BaseDestroyedEvent(_Event):
    type: Literal["base_destroyed"] = "base_destroyed"
    region_id: str
    owner_id: str


class ScoreChangedEvent(_Event):
    type: Literal["score_changed"] = "score_changed"
    player_id: str
    delta: int
    reason: ScoreReason
    new_total: int


class PlayerGoneEvent(_Event):
    type: Literal["player_eliminated", "player_surrendered"]
    player_id: str


class TerritoryNeutralizedEvent(_Event):
    type: Literal["territory_neutralized"] = "territory_neutralized"
    region_id: str
    former_owner_id: str


class FinalTiebreakStartedEvent(_Event):
    type: Literal["final_tiebreak_started"] = "final_tiebreak_started"
    contenders: tuple[str, ...]


ClientEvent = Annotated[
    PlayerJoinedEvent
    | PlayerLeftEvent
    | GameStartedEvent
    | BasesAssignedEvent
    | WarmupStartedEvent
    | GameFinishedEvent
    | GameAbortedEvent
    | QuestionPresentedEvent
    | PlayerAnsweredEvent
    | QuestionResolvedEvent
    | RoundEvent
    | PicksGrantedEvent
    | TerritoryClaimedEvent
    | TurnStartedEvent
    | TurnEndedEvent
    | AttackDeclaredEvent
    | DuelResolvedEvent
    | TiebreakStartedEvent
    | TerritoryCapturedEvent
    | NeutralCapturedEvent
    | NeutralAttackFailedEvent
    | DefenseHeldEvent
    | BaseDamagedEvent
    | BaseDestroyedEvent
    | ScoreChangedEvent
    | PlayerGoneEvent
    | TerritoryNeutralizedEvent
    | FinalTiebreakStartedEvent,
    Field(discriminator="type"),
]
```

- [ ] **Step 4: Write the projection**

`backend/src/triviador/api/projection/events.py`:

```python
"""One domain event, one client event or nothing — decided per viewer.

`PROJECTED` and `WITHHELD` are declared rather than inferred, and a test
asserts their union is exactly the `GameEvent` union. The failure that
guards against is silent: a new event type that fell through to a `None`
default would simply never appear in any client, with nothing reporting it.
"""

from typing import assert_never

from triviador.api.projection.viewer import ViewerContext
from triviador.api.schemas.events import (
    AttackDeclaredEvent,
    BaseDamagedEvent,
    BaseDestroyedEvent,
    BasesAssignedEvent,
    ClientEvent,
    DefenseHeldEvent,
    DuelResolvedEvent,
    FinalTiebreakStartedEvent,
    GameAbortedEvent,
    GameFinishedEvent,
    GameStartedEvent,
    NeutralAttackFailedEvent,
    NeutralCapturedEvent,
    PicksGrantedEvent,
    PlayerAnsweredEvent,
    PlayerGoneEvent,
    PlayerJoinedEvent,
    PlayerLeftEvent,
    QuestionPresentedEvent,
    QuestionResolvedEvent,
    RoundEvent,
    ScoreChangedEvent,
    TerritoryCapturedEvent,
    TerritoryClaimedEvent,
    TerritoryNeutralizedEvent,
    TiebreakStartedEvent,
    TurnEndedEvent,
    TurnStartedEvent,
    WarmupStartedEvent,
)
from triviador.api.schemas.games import SubmittedValue
from triviador.domain.game import events as ev
from triviador.domain.game.state import ChoiceAnswer

# Nothing derived: both are written out, and `test_projection_events.py`
# asserts their union is the whole `GameEvent` union.
WITHHELD = {"GameCreated", "QuestionPoolDrawn", "AnswerWindowClosed"}
PROJECTED = {
    "PlayerJoined", "PlayerLeft", "GameStarted", "BasesAssigned", "MediaWarmupStarted",
    "GameFinished", "GameAborted", "QuestionPresented", "AnswerSubmitted", "QuestionResolved",
    "ExpansionRoundStarted", "PicksGranted", "TerritoryClaimed", "ExpansionRoundCompleted",
    "BattleRoundStarted", "TurnStarted", "TurnSkipped", "TurnAborted", "AttackDeclared",
    "DuelResolved", "TiebreakStarted", "TerritoryCaptured", "NeutralTerritoryCaptured",
    "NeutralAttackFailed", "DefenseHeld", "BaseDamaged", "BaseDestroyed",
    "BattleRoundCompleted", "ScoreChanged", "PlayerEliminated", "PlayerSurrendered",
    "TerritoryNeutralized", "FinalTiebreakStarted",
}


def _own_value(event: ev.AnswerSubmitted, viewer: ViewerContext) -> SubmittedValue | None:
    if viewer.player_id != event.player_id:
        return None
    value = event.answer.value
    if isinstance(value, ChoiceAnswer):
        return SubmittedValue(kind="choice", idx=value.idx)
    return SubmittedValue(kind="numeric", value=str(value.value))


def project_event(event: ev.GameEvent, viewer: ViewerContext) -> ClientEvent | None:
    match event:
        # --- withheld ------------------------------------------------------
        case ev.GameCreated():
            # Never folded and never in a published batch (§6.2 writes it
            # directly at genesis); listed so the decision is explicit.
            return None
        case ev.QuestionPoolDrawn():
            # The whole match, answers included.
            return None
        case ev.AnswerWindowClosed():
            # Mechanical: the snapshot's turn already changed, and a
            # narration line for it would say nothing a player can see.
            return None

        # --- lifecycle -----------------------------------------------------
        case ev.PlayerJoined(player_id=pid, display_name=name, seat=seat):
            return PlayerJoinedEvent(player_id=str(pid), display_name=name, seat=seat)
        case ev.PlayerLeft(player_id=pid):
            return PlayerLeftEvent(player_id=str(pid))
        case ev.GameStarted(turn_order=order):
            return GameStartedEvent(turn_order=tuple(str(p) for p in order))
        case ev.BasesAssigned(assignments=assignments):
            return BasesAssignedEvent(
                assignments={str(p): str(r) for p, r in assignments.items()}
            )
        case ev.MediaWarmupStarted(deadline=deadline):
            return WarmupStartedEvent(deadline_id=int(deadline.id))
        case ev.GameFinished(winner_id=winner, final_scores=scores):
            return GameFinishedEvent(
                winner_id=None if winner is None else str(winner),
                final_scores={str(p): s for p, s in scores.items()},
            )
        case ev.GameAborted(reason=reason):
            return GameAbortedEvent(reason=reason)

        # --- questions -----------------------------------------------------
        case ev.QuestionPresented(deadline=deadline):
            return QuestionPresentedEvent(deadline_id=int(deadline.id))
        case ev.AnswerSubmitted(player_id=pid):
            return PlayerAnsweredEvent(player_id=str(pid), your_answer=_own_value(event, viewer))
        case ev.QuestionResolved(
            correct_choice_index=idx, correct_value=value, ranking=ranking,
            correct_players=correct,
        ):
            return QuestionResolvedEvent(
                correct_choice_index=idx,
                correct_value=None if value is None else str(value),
                ranking=tuple(str(p) for p in ranking),
                correct_players=tuple(str(p) for p in correct),
            )

        # --- expansion -----------------------------------------------------
        case ev.ExpansionRoundStarted(round_no=n):
            return RoundEvent(type="round_started", phase="expansion", round_no=n)
        case ev.ExpansionRoundCompleted(round_no=n):
            return RoundEvent(type="round_completed", phase="expansion", round_no=n)
        case ev.PicksGranted(pick_order=order, grants=grants, deadline=deadline):
            return PicksGrantedEvent(
                pick_order=tuple(str(p) for p in order),
                grants={str(p): n for p, n in grants.items()},
                deadline_id=int(deadline.id),
            )
        case ev.TerritoryClaimed(
            player_id=pid, region_id=rid, acquisition=acq, automatic=automatic
        ):
            return TerritoryClaimedEvent(
                player_id=str(pid), region_id=str(rid), acquisition=acq, automatic=automatic
            )

        # --- battle --------------------------------------------------------
        case ev.BattleRoundStarted(round_no=n):
            return RoundEvent(type="round_started", phase="battle", round_no=n)
        case ev.BattleRoundCompleted(round_no=n):
            return RoundEvent(type="round_completed", phase="battle", round_no=n)
        case ev.TurnStarted(attacker_id=pid, deadline=deadline):
            return TurnStartedEvent(attacker_id=str(pid), deadline_id=int(deadline.id))
        case ev.TurnSkipped(attacker_id=pid, reason=reason):
            return TurnEndedEvent(type="turn_skipped", attacker_id=str(pid), reason=reason)
        case ev.TurnAborted(reason=reason):
            return TurnEndedEvent(type="turn_aborted", attacker_id=None, reason=reason)
        case ev.AttackDeclared(attacker_id=a, defender_id=d, region_id=rid):
            return AttackDeclaredEvent(
                attacker_id=str(a),
                defender_id=None if d is None else str(d),
                region_id=str(rid),
            )
        case ev.DuelResolved(winner_id=winner):
            return DuelResolvedEvent(winner_id=None if winner is None else str(winner))
        case ev.TiebreakStarted(region_id=rid):
            return TiebreakStartedEvent(region_id=str(rid))
        case ev.TerritoryCaptured(
            region_id=rid, from_player_id=src, to_player_id=dst, acquisition=acq
        ):
            return TerritoryCapturedEvent(
                region_id=str(rid),
                from_player_id=None if src is None else str(src),
                to_player_id=str(dst),
                acquisition=acq,
            )
        case ev.NeutralTerritoryCaptured(region_id=rid, player_id=pid):
            return NeutralCapturedEvent(region_id=str(rid), player_id=str(pid))
        case ev.NeutralAttackFailed(region_id=rid, attacker_id=pid):
            return NeutralAttackFailedEvent(region_id=str(rid), attacker_id=str(pid))
        case ev.DefenseHeld(region_id=rid, defender_id=pid):
            return DefenseHeldEvent(region_id=str(rid), defender_id=str(pid))
        case ev.BaseDamaged(region_id=rid, hp_remaining=hp):
            return BaseDamagedEvent(region_id=str(rid), hp_remaining=hp)
        case ev.BaseDestroyed(region_id=rid, owner_id=pid):
            return BaseDestroyedEvent(region_id=str(rid), owner_id=str(pid))

        # --- scoring and terminal ------------------------------------------
        case ev.ScoreChanged(player_id=pid, delta=delta, reason=reason, new_total=total):
            return ScoreChangedEvent(
                player_id=str(pid), delta=delta, reason=reason, new_total=total
            )
        case ev.PlayerEliminated(player_id=pid):
            return PlayerGoneEvent(type="player_eliminated", player_id=str(pid))
        case ev.PlayerSurrendered(player_id=pid):
            return PlayerGoneEvent(type="player_surrendered", player_id=str(pid))
        case ev.TerritoryNeutralized(region_id=rid, former_owner_id=pid):
            return TerritoryNeutralizedEvent(region_id=str(rid), former_owner_id=str(pid))
        case ev.FinalTiebreakStarted(contenders=contenders):
            return FinalTiebreakStartedEvent(contenders=tuple(str(p) for p in contenders))
        case _:
            assert_never(event)
```

- [ ] **Step 5: Run the tests**

Run: `cd backend && uv run pytest tests/api -v --no-cov && uv run mypy --strict`
Expected: PASS. If `mypy` reports the `assert_never` as reachable, an event type is missing a branch — which is the gate working.

- [ ] **Step 6: Commit**

```bash
git add backend/src/triviador/api backend/tests/api
git commit -m "feat(api): event projection — narration per viewer, pool and genesis withheld"
```

---

## Task 13: The socket envelope — strict, actorless, and windowed only where it should be

**Files:**
- Create: `backend/src/triviador/api/schemas/ws.py`
- Test: `backend/tests/api/test_ws_schemas.py`

**Interfaces:**
- Consumes: `ClientEvent`, `ClientGameState`, `GameSummary`-shaped DTOs (`LobbyGame`, added here), `ApiErrorCode`, `RejectCode`.
- Produces: `ClientMessage` (union: `SubscribeFrame`, `UnsubscribeFrame`, `ResyncFrame`, `PingFrame`, `SubmitAnswerFrame`, `PickRegionFrame`, `SelectTargetFrame`, `SurrenderFrame`); `ServerMessage` (union: `HelloMessage`, `SnapshotMessage`, `UpdateMessage`, `PresenceMessage`, `LobbyMessage`, `ErrorMessage`, `PongMessage`); `CLIENT_MESSAGE_ADAPTER: TypeAdapter[ClientMessage]`; `AnswerPayload`; `TOPIC_PATTERN`; `game_topic(game_id) -> str`; `LOBBY_TOPIC`.

- [ ] **Step 1: Write the failing test**

`backend/tests/api/test_ws_schemas.py`:

```python
"""§6.5: frames are strict, and they carry no actor.

Two separate properties, both asserted. The field is *unacceptable* — a
frame carrying `actor_id` is rejected before anything reads it — and
identity comes from the principal, which Task 16 asserts at the endpoint.
An earlier formulation of this ("a frame naming another player still acts
as the session's user") contradicted strict validation and is not what
either test says.
"""

import json
from typing import get_args

import pytest
from pydantic import ValidationError

from triviador.api.schemas.ws import (
    CLIENT_MESSAGE_ADAPTER,
    ClientMessage,
    ServerMessage,
    game_topic,
)

WINDOWED = {"submit_answer", "pick_region", "select_attack_target"}


def frame(**kw: object) -> str:
    return json.dumps(kw)


def parse(**kw: object) -> ClientMessage:
    return CLIENT_MESSAGE_ADAPTER.validate_json(frame(**kw))


def test_a_valid_answer_frame_parses() -> None:
    message = parse(
        type="submit_answer",
        command_id="c1",
        game_id="g1",
        deadline_id=7,
        payload={"kind": "numeric", "value": "42.5"},
    )
    # Two narrowing steps, not one: `AnswerPayload` is itself a
    # `ChoiceAnswerPayload | NumericAnswerPayload` union and the choice
    # variant has no `.value`. Narrowing rather than suppressing also makes
    # this assert *which* frame the parser produced, not merely that
    # something with a `.value` came back.
    assert isinstance(message, SubmitAnswerFrame)
    assert isinstance(message.payload, NumericAnswerPayload)
    assert message.payload.value == "42.5"


def test_a_numeric_answer_arrives_as_a_string() -> None:
    """JSON has one number type and it is a float: `0.1` does not survive
    the trip as a `Decimal`. Every number this API compares for equality is
    a decimal string."""
    with pytest.raises(ValidationError):
        parse(type="submit_answer", command_id="c1", game_id="g1", deadline_id=7,
              payload={"kind": "numeric", "value": 42.5})


@pytest.mark.parametrize(
    "value",
    ["forty-two", "NaN", "nan", "-NaN", "sNaN", "Infinity", "-Infinity", "inf", "-inf"],
)
def test_a_numeric_value_that_is_not_a_finite_number_is_rejected(value: str) -> None:
    """`Decimal("NaN")` parses. It then reaches `_rank_numeric`, where an
    ordering comparison against it raises `InvalidOperation` *inside*
    `decide` — which §5.5 treats as a fault and quarantines the game for.
    One frame, one dead game, from an authenticated player who only had to
    type four characters."""
    with pytest.raises(ValidationError):
        parse(type="submit_answer", command_id="c1", game_id="g1", deadline_id=7,
              payload={"kind": "numeric", "value": value})


@pytest.mark.parametrize(
    "kw",
    [
        {"type": "ping"},
        {"type": "subscribe", "topic": "lobby"},
        {"type": "surrender", "command_id": "c1", "game_id": "g1"},
    ],
)
def test_an_extra_field_on_any_frame_is_rejected(kw: dict[str, object]) -> None:
    """`extra="forbid"` everywhere. Omitting `actor_id` from a schema is
    worth nothing if unknown keys are silently ignored (§6.5)."""
    with pytest.raises(ValidationError):
        parse(**kw, unexpected="x")


@pytest.mark.parametrize(
    "payload",
    [
        {"kind": "numeric", "value": "1", "unexpected": "x"},
        {"kind": "choice", "idx": 1, "unexpected": "x"},
    ],
    ids=["numeric-payload", "choice-payload"],
)
def test_an_extra_field_inside_a_payload_is_rejected(payload: dict[str, object]) -> None:
    """The nested half, and the one that actually gets missed.

    A strict outer frame wrapping a permissive `payload` would accept an
    unknown key one level down — which is where an actor would go if
    anybody tried. Testing only the top level leaves every payload model
    free to extend bare `BaseModel` with nothing failing.
    """
    with pytest.raises(ValidationError):
        parse(
            type="submit_answer", command_id="c1", game_id="g1", deadline_id=7, payload=payload
        )


def test_an_extra_field_inside_a_region_payload_is_rejected() -> None:
    """`RegionPayload` is a separate model from the answer payloads, so a
    regression could hit it alone."""
    with pytest.raises(ValidationError):
        parse(
            type="pick_region",
            command_id="c1",
            game_id="g1",
            deadline_id=7,
            payload={"region_id": "r3", "unexpected": "x"},
        )


@pytest.mark.parametrize(
    "kw",
    [
        {"type": "surrender", "command_id": "c1", "game_id": "g1"},
        {"type": "submit_answer", "command_id": "c1", "game_id": "g1", "deadline_id": 7,
         "payload": {"kind": "choice", "idx": 1}},
    ],
)
def test_a_frame_carrying_an_actor_is_rejected_outright(kw: dict[str, object]) -> None:
    """The one that matters. Identity is derived from the session, and a
    frame that even mentions an actor is refused rather than sanitized."""
    with pytest.raises(ValidationError):
        parse(**kw, actor_id="somebody-else")


def test_an_actor_hidden_inside_a_payload_is_rejected_too() -> None:
    """Where an actor would actually be smuggled, once the top level is
    known to be strict — one level deeper than the test above looks."""
    with pytest.raises(ValidationError):
        parse(
            type="submit_answer",
            command_id="c1",
            game_id="g1",
            deadline_id=7,
            payload={"kind": "numeric", "value": "1", "actor_id": "somebody-else"},
        )


def test_only_the_windowed_commands_declare_a_deadline() -> None:
    """§6.5, checked against the models rather than by inspection: a
    `deadline_id` on surrender would be a window identity for a command
    that has no window, and the guard pipeline would have to decide what to
    do with it."""
    for model in get_args(get_args(ClientMessage)[0]):
        kind = model.model_fields["type"].default
        has_deadline = "deadline_id" in model.model_fields
        assert has_deadline == (kind in WINDOWED), kind


def test_expire_deadline_is_not_a_client_frame() -> None:
    """§6.5: server-internal. A client that could expire its own window
    could end an opponent's answer time."""
    kinds = {m.model_fields["type"].default for m in get_args(get_args(ClientMessage)[0])}
    assert "expire_deadline" not in kinds
    assert "abort_game" not in kinds
    assert "join_game" not in kinds  # REST, per §8.2
    assert "start_game" not in kinds


def test_a_missing_deadline_on_a_windowed_command_is_rejected(  ) -> None:
    with pytest.raises(ValidationError):
        parse(type="pick_region", command_id="c1", game_id="g1",
              payload={"region_id": "r3"})


@pytest.mark.parametrize(
    "topic", ["lobby", "game:0123abcd", "admin:games", "game:", "game:../x", ""]
)
def test_only_the_two_spec_1_topics_are_accepted(topic: str) -> None:
    """`admin:*` is Spec 2 (§8.1). Accepting it now would mean a topic with
    no authorization rule behind it."""
    ok = topic in {"lobby", "game:0123abcd"}
    try:
        parse(type="subscribe", topic=topic)
    except ValidationError:
        assert not ok
    else:
        assert ok


def test_a_game_topic_is_built_the_one_way() -> None:
    assert game_topic("g1") == "game:g1"


def test_no_server_message_shares_a_base_class_with_a_domain_type() -> None:
    for model in get_args(get_args(ServerMessage)[0]):
        assert not any(b.__module__.startswith("triviador.domain") for b in model.__mro__[1:])
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd backend && uv run pytest tests/api/test_ws_schemas.py -v --no-cov`
Expected: FAIL — `triviador.api.schemas.ws` does not exist.

- [ ] **Step 3: Write the envelope**

`backend/src/triviador/api/schemas/ws.py`:

```python
"""The socket envelope, both directions.

One flat discriminated union per direction. §6.5 enumerates "surrender,
subscribe, unsubscribe, or ping" in one breath when saying where
`deadline_id` may not appear, which only parses if transport frames and
commands share a `type` discriminator — so they do.
"""

from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, field_validator

from triviador.api.errors import ApiErrorCode
from triviador.api.schemas.events import ClientEvent
from triviador.api.schemas.games import ClientGameState
from triviador.domain.game.actions import RejectCode

LOBBY_TOPIC = "lobby"
# `admin:*` is Spec 2 (§8.1) and is deliberately unmatched: a topic the
# parser accepts is a topic something has to authorize.
TOPIC_PATTERN = r"^(lobby|game:[A-Za-z0-9_-]{1,64})$"

Topic = Annotated[str, Field(pattern=TOPIC_PATTERN)]
CommandId = Annotated[str, Field(min_length=1, max_length=64)]
GameIdField = Annotated[str, Field(min_length=1, max_length=64)]


def game_topic(game_id: str) -> str:
    return f"game:{game_id}"


class _Frame(BaseModel):
    """Every client frame. `extra="forbid"` is the property §6.5 requires,
    and it is what makes `actor_id` unacceptable rather than ignored."""

    model_config = ConfigDict(extra="forbid")


class SubscribeFrame(_Frame):
    type: Literal["subscribe"] = "subscribe"
    topic: Topic


class UnsubscribeFrame(_Frame):
    type: Literal["unsubscribe"] = "unsubscribe"
    topic: Topic


class ResyncFrame(_Frame):
    """§8.5: the client asks for a fresh snapshot rather than catching up on
    events. A whole game state is a couple of kilobytes."""

    type: Literal["resync"] = "resync"
    topic: Topic


class PingFrame(_Frame):
    type: Literal["ping"] = "ping"


class ChoiceAnswerPayload(_Frame):
    kind: Literal["choice"] = "choice"
    idx: int = Field(ge=0, le=15)


class NumericAnswerPayload(_Frame):
    kind: Literal["numeric"] = "numeric"
    value: str = Field(min_length=1, max_length=40)

    @field_validator("value")
    @classmethod
    def _decimal(cls, value: str) -> str:
        """Finite, not merely parseable.

        `Decimal("NaN")` and `Decimal("Infinity")` both construct without
        raising, and they do not stay harmless: `_rank_numeric` sorts on
        `(wrong?, abs(value - correct), elapsed_ms, seat)`, and an ordering
        comparison against a `Decimal` NaN raises `InvalidOperation` — from
        inside `decide`, which §5.5 classifies as a fault and quarantines
        the game for. One client frame would take a healthy game down.
        Infinity does not raise, but it sorts last-but-one forever and is
        not an answer to any question.
        """
        try:
            parsed = Decimal(value)
        except InvalidOperation as exc:
            raise ValueError("not a decimal number") from exc
        if not parsed.is_finite():
            raise ValueError("must be a finite number")
        return value


AnswerPayload = Annotated[
    ChoiceAnswerPayload | NumericAnswerPayload, Field(discriminator="kind")
]


class RegionPayload(_Frame):
    region_id: str = Field(min_length=1, max_length=64)


class _Command(_Frame):
    command_id: CommandId
    game_id: GameIdField


class SubmitAnswerFrame(_Command):
    type: Literal["submit_answer"] = "submit_answer"
    deadline_id: int
    payload: AnswerPayload


class PickRegionFrame(_Command):
    type: Literal["pick_region"] = "pick_region"
    deadline_id: int
    payload: RegionPayload


class SelectTargetFrame(_Command):
    type: Literal["select_attack_target"] = "select_attack_target"
    deadline_id: int
    payload: RegionPayload


class SurrenderFrame(_Command):
    """No `deadline_id` and no `payload`: surrender is not windowed and
    carries nothing. An empty `payload: {}` would be a field whose only
    possible value is one the server ignores."""

    type: Literal["surrender"] = "surrender"


ClientMessage = Annotated[
    SubscribeFrame
    | UnsubscribeFrame
    | ResyncFrame
    | PingFrame
    | SubmitAnswerFrame
    | PickRegionFrame
    | SelectTargetFrame
    | SurrenderFrame,
    Field(discriminator="type"),
]

CLIENT_MESSAGE_ADAPTER: TypeAdapter[ClientMessage] = TypeAdapter(ClientMessage)


# --- server → client --------------------------------------------------------


class _Message(BaseModel):
    model_config = ConfigDict(extra="forbid")


class HelloMessage(_Message):
    """§8.6: the client refines its clock offset from ping/pong, not from
    this — `server_time` alone embeds one-way network delay."""

    type: Literal["hello"] = "hello"
    server_time: datetime


class PongMessage(_Message):
    type: Literal["pong"] = "pong"
    server_time: datetime


class LobbyGame(_Message):
    game_id: str
    map_id: str
    host_id: str
    status: str
    player_count: int
    max_players: int


class LobbyMessage(_Message):
    type: Literal["lobby.snapshot", "lobby.update"]
    games: tuple[LobbyGame, ...]


class SnapshotMessage(_Message):
    type: Literal["game.snapshot"] = "game.snapshot"
    game_id: str
    seq: int
    state: ClientGameState


class UpdateMessage(_Message):
    """§8.4: the transport unit is the whole committed batch, so the client
    can sequence on `base_seq`/`seq` even though projection drops events."""

    type: Literal["game.update"] = "game.update"
    game_id: str
    base_seq: int
    seq: int
    state: ClientGameState
    events: tuple[ClientEvent, ...]


class PresenceMessage(_Message):
    """§8.3: deliberately not a domain event — no `seq`, not persisted,
    absent from replay."""

    type: Literal["game.presence"] = "game.presence"
    game_id: str
    connected: tuple[str, ...]


class ErrorMessage(_Message):
    """`command_id` is transport correlation only (§8.3): with several
    actions pending the client cannot otherwise tell which one a
    `REGION_NOT_FREE` belongs to. It is never used to retry."""

    type: Literal["error"] = "error"
    command_id: str | None
    code: ApiErrorCode | RejectCode
    message: str


ServerMessage = Annotated[
    HelloMessage
    | PongMessage
    | LobbyMessage
    | SnapshotMessage
    | UpdateMessage
    | PresenceMessage
    | ErrorMessage,
    Field(discriminator="type"),
]
```

- [ ] **Step 4: Run the tests**

Run: `cd backend && uv run pytest tests/api/test_ws_schemas.py -v --no-cov && uv run mypy --strict`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backend/src/triviador/api/schemas/ws.py backend/tests/api/test_ws_schemas.py
git commit -m "feat(api): the socket envelope — strict frames that cannot name an actor"
```

---

## Task 14: The hub — connections, topics, the sender task, and backpressure

**Files:**
- Create: `backend/src/triviador/api/ws/__init__.py`, `backend/src/triviador/api/ws/hub.py`
- Test: `backend/tests/api/test_ws_hub.py`

**Interfaces:**
- Consumes: `AuthenticatedPrincipal`, `SessionId`, `UserId`, `ServerMessage`, `game_topic`.
- Produces: `Socket` (Protocol: `async send_text(str)`, `async close(code)`); `Connection(id, principal, socket, queue_size)` with synchronous `send(message)`, `close(code)`, `topics`; `Hub` with `add`, `remove`, `subscribe`, `unsubscribe`, `subscribers(topic)`, `close_game_subscribers(game_id, code)`, `subscriber_count(game_id)`, `close_sessions(session_ids, code)`, `players_in(game_id)`; `run_sender(connection)`.

- [ ] **Step 1: Write the failing test**

`backend/tests/api/test_ws_hub.py`:

```python
"""§8.6's outbound path: the runtime never awaits a socket write.

Everything the runtime can reach — `send`, `close`, `subscriber_count` — is
synchronous, and the only thing that touches the socket is the sender task.
"""

import asyncio

import pytest

from triviador.api.schemas.ws import HelloMessage, PongMessage
from triviador.api.ws.hub import Connection, Hub, run_sender
from triviador.domain.ids import SessionId, UserId
from triviador.services.identity import AuthenticatedPrincipal, UserRole

T0 = __import__("tests.api.fakes", fromlist=["T0"]).T0


class FakeSocket:
    def __init__(self) -> None:
        self.sent: list[str] = []
        self.closed_with: int | None = None
        self.blocked = asyncio.Event()
        self.blocked.set()

    async def send_text(self, text: str) -> None:
        await self.blocked.wait()
        self.sent.append(text)

    async def close(self, code: int) -> None:
        self.closed_with = code


def principal(user_id: str = "u1") -> AuthenticatedPrincipal:
    return AuthenticatedPrincipal(UserId(user_id), UserRole.PLAYER, SessionId(f"s-{user_id}"))


def a_connection(socket: FakeSocket | None = None, **kw: object) -> Connection:
    return Connection(
        id=str(kw.get("id", "c1")),
        principal=principal(str(kw.get("user_id", "u1"))),
        socket=socket or FakeSocket(),
        queue_size=int(kw.get("queue_size", 64)),  # type: ignore[arg-type]
    )


async def test_a_message_reaches_the_socket_through_the_sender_task() -> None:
    socket = FakeSocket()
    connection = a_connection(socket)
    task = asyncio.create_task(run_sender(connection))
    connection.send(HelloMessage(server_time=T0))
    await asyncio.sleep(0)
    connection.close(1000)
    await task
    assert '"hello"' in socket.sent[0]
    assert socket.closed_with == 1000


def test_send_is_synchronous_and_returns_nothing_awaitable() -> None:
    """The contract `Broadcaster` exists to enforce: `publish` is a `def`,
    so anything it calls must be too. A coroutine here would be silently
    never awaited and the message would simply never arrive."""
    connection = a_connection()
    assert connection.send(HelloMessage(server_time=T0)) is None


async def test_a_full_outbound_queue_closes_that_subscriber_with_4408() -> None:
    """§8.6's backpressure rule and Spec 1 §12.2's scenario: a client that
    never reads must not stall the loop. It is closed; it reconnects and
    takes a snapshot (§8.5)."""
    socket = FakeSocket()
    socket.blocked.clear()  # the sender parks on the first write
    connection = a_connection(socket, queue_size=2)
    task = asyncio.create_task(run_sender(connection))
    for _ in range(5):
        connection.send(HelloMessage(server_time=T0))
    socket.blocked.set()
    await task
    assert socket.closed_with == 4408


async def test_closing_discards_whatever_was_still_queued() -> None:
    """The queue is full by definition when 4408 fires, so the close
    sentinel has to displace something. Delivering a partial backlog to a
    connection that is being closed helps nobody and the sentinel must not
    itself raise `QueueFull`."""
    socket = FakeSocket()
    socket.blocked.clear()
    connection = a_connection(socket, queue_size=1)
    task = asyncio.create_task(run_sender(connection))
    connection.send(HelloMessage(server_time=T0))
    connection.send(PongMessage(server_time=T0))
    connection.send(PongMessage(server_time=T0))
    socket.blocked.set()
    await task
    assert socket.closed_with == 4408
    assert len(socket.sent) <= 1


async def test_a_socket_that_raises_ends_the_sender_rather_than_spinning() -> None:
    """§8.6's sender exits on any send failure, not only on the sentinel.

    A socket that raised is a socket that is gone, and continuing to drain
    into it is a task that never ends — which is the exact wedged-connection
    failure the bounded queue exists to prevent, arriving by another door.
    """

    class BrokenSocket(FakeSocket):
        async def send_text(self, text: str) -> None:
            raise ConnectionResetError("peer went away")

    connection = a_connection(BrokenSocket())
    task = asyncio.create_task(run_sender(connection))
    connection.send(HelloMessage(server_time=T0))
    await asyncio.wait_for(task, timeout=1)
    assert connection.close_code == 1011


async def test_a_send_failure_does_not_overwrite_a_close_code_already_set() -> None:
    """"First code wins" meeting the failure path. A connection already
    closing with 4408 must not be relabelled 1011 on its way out, or the
    client is told to reconnect with backoff when it should reconnect at
    once."""

    class BrokenSocket(FakeSocket):
        async def send_text(self, text: str) -> None:
            raise ConnectionResetError("peer went away")

    connection = a_connection(BrokenSocket())
    task = asyncio.create_task(run_sender(connection))
    connection.send(HelloMessage(server_time=T0))
    connection.close_code = 4408
    await asyncio.wait_for(task, timeout=1)
    assert connection.close_code == 4408


def test_a_second_close_is_ignored() -> None:
    """Origins, the broadcaster and the read loop can all decide to close
    the same connection; the first code wins, as it does for origins."""
    connection = a_connection()
    connection.close(4403)
    connection.close(1011)
    assert connection.close_code == 4403


def test_subscribing_indexes_the_connection_under_its_topic() -> None:
    hub, connection = Hub(), a_connection()
    hub.add(connection)
    hub.subscribe(connection, "game:g1")
    assert list(hub.subscribers("game:g1")) == [connection]
    assert hub.subscriber_count("g1") == 1


def test_unsubscribing_removes_it_and_leaves_no_empty_topic_behind() -> None:
    """An index that accumulates empty sets is a slow leak in a process
    that is meant to run for months."""
    hub, connection = Hub(), a_connection()
    hub.add(connection)
    hub.subscribe(connection, "game:g1")
    hub.unsubscribe(connection, "game:g1")
    assert hub.subscriber_count("g1") == 0
    assert "game:g1" not in hub.topics


def test_removing_a_connection_removes_every_subscription_it_held() -> None:
    hub, connection = Hub(), a_connection()
    hub.add(connection)
    hub.subscribe(connection, "game:g1")
    hub.subscribe(connection, "lobby")
    hub.remove(connection)
    assert hub.subscriber_count("g1") == 0
    assert list(hub.subscribers("lobby")) == []


def test_closing_a_games_subscribers_uses_the_code_it_was_given() -> None:
    """`GameSubscriberControl`: the manager asks, the hub acts. 1011 on
    quarantine, 1001 on shutdown (§5.6) — the manager chooses, because only
    it knows which."""
    hub = Hub()
    here, elsewhere = a_connection(id="c1"), a_connection(id="c2", user_id="u2")
    for connection in (here, elsewhere):
        hub.add(connection)
    hub.subscribe(here, "game:g1")
    hub.subscribe(elsewhere, "game:g2")
    hub.close_game_subscribers("g1", 1011)
    assert here.close_code == 1011
    assert elsewhere.close_code is None


def test_revoking_a_session_closes_exactly_that_connection_with_4401() -> None:
    """§6.5's session revocation, and Spec 1 §7's reason opaque tokens were
    chosen. Plan 7's deactivate endpoint is the caller; the mechanism is
    testable now."""
    hub = Hub()
    one, two = a_connection(id="c1", user_id="u1"), a_connection(id="c2", user_id="u2")
    for connection in (one, two):
        hub.add(connection)
    hub.close_sessions((SessionId("s-u1"),), 4401)
    assert one.close_code == 4401
    assert two.close_code is None


def test_presence_lists_the_participants_currently_connected() -> None:
    hub = Hub()
    one, two = a_connection(id="c1", user_id="u1"), a_connection(id="c2", user_id="u2")
    for connection in (one, two):
        hub.add(connection)
        hub.subscribe(connection, "game:g1")
    assert set(hub.players_in("g1")) == {"u1", "u2"}


def test_two_tabs_of_one_user_count_once_in_presence(  ) -> None:
    """Presence is about people, not sockets: §8.1 is one socket per browser
    *tab*, and a player with two tabs open is one player in the room."""
    hub = Hub()
    for i in (1, 2):
        connection = a_connection(id=f"c{i}", user_id="u1")
        hub.add(connection)
        hub.subscribe(connection, "game:g1")
    assert hub.players_in("g1") == ("u1",)
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd backend && uv run pytest tests/api/test_ws_hub.py -v --no-cov`
Expected: FAIL — `triviador.api.ws.hub` does not exist.

- [ ] **Step 3: Write the hub**

`backend/src/triviador/api/ws/hub.py`:

```python
"""Connections, topics, and the one task allowed to touch a socket.

The shape follows §8.6 exactly:

    runtime ──put_nowait──► bounded outbound queue (~64) ──► sender ──► socket
                                  │ QueueFull
                                  ▼
                            close(4408)

Everything reachable from the runtime — `Connection.send`, `close`, and the
hub's `close_game_subscribers` / `subscriber_count` — is a `def`. That is
not a stylistic choice: `Broadcaster.publish` is synchronous precisely so
that awaiting a socket write from the consumer loop cannot compile.
"""

import logging
from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field
from typing import Protocol

from asyncio import Queue, QueueEmpty, QueueFull

from triviador.api.schemas.ws import ServerMessage, game_topic
from triviador.domain.ids import SessionId

logger = logging.getLogger(__name__)


class Socket(Protocol):
    """What the hub needs of a WebSocket. Starlette's `WebSocket` satisfies
    it structurally, and so does a two-attribute test double."""

    async def send_text(self, text: str) -> None: ...
    async def close(self, code: int) -> None: ...


@dataclass(frozen=True)
class _Close:
    code: int


@dataclass
class Connection:
    id: str
    principal: "AuthenticatedPrincipal"
    socket: Socket
    queue_size: int = 64
    topics: set[str] = field(default_factory=set)
    close_code: int | None = None
    _outbound: "Queue[ServerMessage | _Close]" = field(init=False)

    def __post_init__(self) -> None:
        self._outbound = Queue(maxsize=self.queue_size)

    def send(self, message: ServerMessage) -> None:
        """Never blocks, never raises, never awaits."""
        if self.close_code is not None:
            return
        try:
            self._outbound.put_nowait(message)
        except QueueFull:
            # §8.6: a client that is not reading is closed, not waited for.
            # It reconnects and takes a snapshot, which is cheaper and more
            # correct than an unbounded buffer.
            logger.info("connection %s: outbound queue full, closing 4408", self.id)
            self.close(4408)

    def close(self, code: int) -> None:
        """First code wins, like an origin's first outcome.

        The sentinel is enqueued the ordinary way first, and the queue is
        drained **only** if that fails. Draining unconditionally would be
        simpler and is wrong: a graceful close would then discard whatever
        was already queued — a `hello` a short-lived connection never got
        to send, say — and the caller would have to reach for a scheduling
        yield to get it delivered, which is a guarantee no amount of
        `sleep(0)` actually provides once the socket suspends mid-write.

        The 4408 path still reaches the drain by definition: the queue
        being full is *why* we are closing, so `put_nowait` raises and the
        sentinel must displace something, or the connection stays open
        forever with nobody reading it.
        """
        if self.close_code is not None:
            return
        self.close_code = code
        try:
            self._outbound.put_nowait(_Close(code))
        except QueueFull:
            while True:
                try:
                    self._outbound.get_nowait()
                except QueueEmpty:
                    break
            self._outbound.put_nowait(_Close(code))

    async def next_outbound(self) -> "ServerMessage | _Close":
        return await self._outbound.get()


async def run_sender(connection: Connection) -> None:
    """The only thing that touches the socket (§8.6).

    Exits on the close sentinel, and on any send failure — a socket that
    raised is a socket that is gone, and continuing to drain into it is a
    task that never ends.
    """
    while True:
        item = await connection.next_outbound()
        if isinstance(item, _Close):
            try:
                await connection.socket.close(item.code)
            except Exception:  # noqa: BLE001 — a dead socket is the normal case here
                logger.debug("connection %s: close failed on a dead socket", connection.id)
            return
        try:
            await connection.socket.send_text(item.model_dump_json())
        except Exception:  # noqa: BLE001
            logger.info("connection %s: send failed, ending sender", connection.id)
            connection.close_code = connection.close_code or 1011
            return


class Hub:
    """Connections by id, and the topic index over them."""

    def __init__(self) -> None:
        self.connections: dict[str, Connection] = {}
        self.topics: dict[str, set[str]] = {}

    def add(self, connection: Connection) -> None:
        self.connections[connection.id] = connection

    def remove(self, connection: Connection) -> None:
        self.connections.pop(connection.id, None)
        for topic in list(connection.topics):
            self.unsubscribe(connection, topic)

    def subscribe(self, connection: Connection, topic: str) -> None:
        connection.topics.add(topic)
        self.topics.setdefault(topic, set()).add(connection.id)

    def unsubscribe(self, connection: Connection, topic: str) -> None:
        connection.topics.discard(topic)
        holders = self.topics.get(topic)
        if holders is None:
            return
        holders.discard(connection.id)
        if not holders:
            # Empty sets are a slow leak in a process meant to run for
            # months: one entry per game ever played.
            del self.topics[topic]

    def subscribers(self, topic: str) -> Iterator[Connection]:
        for connection_id in tuple(self.topics.get(topic, ())):
            connection = self.connections.get(connection_id)
            if connection is not None and connection.close_code is None:
                yield connection

    # --- GameSubscriberControl ---------------------------------------------

    def close_game_subscribers(self, game_id: str, code: int) -> None:
        for connection in tuple(self.subscribers(game_topic(game_id))):
            connection.close(code)

    def subscriber_count(self, game_id: str) -> int:
        return sum(1 for _ in self.subscribers(game_topic(game_id)))

    # --- identity-driven closes --------------------------------------------

    def close_sessions(self, session_ids: Iterable[SessionId], code: int) -> None:
        """§6.5: session revocation closes with `4401`."""
        wanted = set(session_ids)
        for connection in tuple(self.connections.values()):
            if connection.principal.session_id in wanted:
                connection.close(code)

    def players_in(self, game_id: str) -> tuple[str, ...]:
        """Presence is per person, not per socket (§8.1: one socket per
        browser *tab*)."""
        seen: dict[str, None] = {}
        for connection in self.subscribers(game_topic(game_id)):
            seen.setdefault(str(connection.principal.user_id), None)
        return tuple(seen)
```

Add `from triviador.services.identity import AuthenticatedPrincipal` and drop the quotes on the annotation; the forward reference above is only to keep the import list readable in this listing.

- [ ] **Step 4: Run the tests**

Run: `cd backend && uv run pytest tests/api/test_ws_hub.py -v --no-cov && uv run mypy --strict`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backend/src/triviador/api/ws backend/tests/api/test_ws_hub.py
git commit -m "feat(api): the hub — topic index, sender task, and 4408 backpressure"
```

---

## Task 15: The broadcaster, and an origin that never holds a future

**Files:**
- Create: `backend/src/triviador/api/ws/broadcaster.py`, `backend/src/triviador/api/ws/origins.py`
- Test: `backend/tests/api/test_broadcaster.py`

**Interfaces:**
- Consumes: `Hub`, `Connection`, `project_snapshot`, `project_event`, `viewer_for`, `UpdateMessage`, `SnapshotMessage`, `PresenceMessage`, `ErrorMessage`, `RuntimeCode`, `RejectCode`.
- Produces: `WsBroadcaster(hub, media_base)` implementing `Broadcaster` **and** `GameSubscriberControl`, plus `snapshot_to(connection, game_id, state)` and `presence(game_id)`; `WsOrigin(connection, command_id)` implementing `Origin`.

- [ ] **Step 1: Write the failing test**

First add two module-level helpers to `backend/tests/api/test_ws_hub.py`, so both suites read a connection's outbound queue the same way — and so a production `Connection` never grows test methods:

```python
def queued(connection: Connection) -> list[str]:
    """Every message still sitting in the outbound queue, as JSON text.
    Reads the private queue deliberately and in one place, the same way
    `tests/runtime/conftest.queued_commands` does for the command queue."""
    items = list(connection._outbound._queue)  # type: ignore[attr-defined]
    return [i.model_dump_json() for i in items if not isinstance(i, _Close)]


def parsed(connection: Connection) -> list[dict]:
    return [json.loads(text) for text in queued(connection)]
```

`backend/tests/api/test_broadcaster.py`:

```python
"""§5.5's last row: broadcaster failure never quarantines.

"The commit is durable and memory is correct; destroying a healthy runtime
over a misbehaving socket converts a client problem into a game-wide
outage." So `publish` catches everything, and the two failure modes get
two different close codes.
"""

from dataclasses import replace
from decimal import Decimal

from tests.api.test_ws_hub import FakeSocket, a_connection, parsed
from tests.conftest import full_pool, lobby_state
from triviador.api.ws.broadcaster import WsBroadcaster
from triviador.api.ws.hub import Hub
from triviador.api.ws.origins import WsOrigin
from triviador.domain.game import events as ev
from triviador.domain.game.actions import RejectCode
from triviador.domain.game.state import NumericAnswer, SubmittedAnswer
from triviador.domain.ids import GameId, PlayerId
from triviador.services.ports import Broadcaster, GameSubscriberControl, RuntimeCode

# `mypy --strict` is what actually proves the two ports are satisfied —
# without these the first proof would be Task 17's `GameManager(...)` call.
_broadcaster: Broadcaster = WsBroadcaster(Hub(), media_base="/media")
_subscribers: GameSubscriberControl = WsBroadcaster(Hub(), media_base="/media")


class Boom:
    """Anything the projection touches explodes. Stands in for the whole
    class of "a bug in projection" without needing to author one."""

    def __getattr__(self, name: str) -> object:
        raise RuntimeError("projection exploded")


def hub_with(*user_ids: str) -> tuple[Hub, list]:
    hub = Hub()
    connections = []
    for i, user_id in enumerate(user_ids):
        connection = a_connection(FakeSocket(), id=f"c{i}", user_id=user_id)
        hub.add(connection)
        hub.subscribe(connection, "game:g1")
        connections.append(connection)
    return hub, connections


def playing_state():
    return replace(lobby_state({"p1": 0, "p2": 1}), seq=8, pool=full_pool())


def test_publish_is_synchronous_and_returns_none() -> None:
    """`Broadcaster.publish` is a `def` so the consumer loop cannot await a
    socket write by accident (§8.6). A coroutine here would typecheck
    against nothing and simply never run."""
    hub, _ = hub_with("p1")
    result = WsBroadcaster(hub, media_base="/media").publish(GameId("g1"), 7, playing_state(), ())
    assert result is None


def test_each_subscriber_gets_the_state_projected_for_them() -> None:
    """The reason `publish` takes domain objects: one commit, N different
    payloads, decided here because only the hub knows the viewers."""
    hub, (one, two) = hub_with("p1", "p2")
    event = ev.AnswerSubmitted(PlayerId("p1"), SubmittedAnswer(NumericAnswer(Decimal(99)), 900))
    WsBroadcaster(hub, media_base="/media").publish(GameId("g1"), 7, playing_state(), (event,))
    assert "99" in str(parsed(one)[0]["events"])
    assert "99" not in str(parsed(two)[0]["events"])


def test_the_update_carries_the_batch_boundaries() -> None:
    """§8.4: the client applies when `base_seq == last_seq`, ignores when
    `seq <= last_seq`, and resyncs otherwise. Both numbers are needed for
    that to be decidable at all."""
    hub, (one,) = hub_with("p1")
    WsBroadcaster(hub, media_base="/media").publish(GameId("g1"), 7, playing_state(), ())
    payload = parsed(one)[0]
    assert (payload["type"], payload["base_seq"], payload["seq"]) == ("game.update", 7, 8)


def test_events_that_project_to_none_simply_do_not_appear() -> None:
    hub, (one,) = hub_with("p1")
    WsBroadcaster(hub, media_base="/media").publish(
        GameId("g1"), 7, playing_state(), (ev.QuestionPoolDrawn(full_pool()),)
    )
    assert parsed(one)[0]["events"] == []


def test_a_subscriber_whose_projection_fails_is_closed_with_1011() -> None:
    """§5.5's second table. The connection dies; the game does not."""
    hub, (one,) = hub_with("p1")
    WsBroadcaster(hub, media_base="/media").publish(GameId("g1"), 7, Boom(), ())  # type: ignore[arg-type]
    assert one.close_code == 1011


def test_publish_never_raises_however_badly_projection_fails() -> None:
    """The property the runtime depends on: an exception out of `publish`
    reaches `_apply`'s fault handling and quarantines a game whose state is
    durable and correct."""
    hub, _ = hub_with("p1", "p2")
    WsBroadcaster(hub, media_base="/media").publish(GameId("g1"), 7, Boom(), ())  # type: ignore[arg-type]


def test_one_broken_subscriber_does_not_cost_the_others_their_update() -> None:
    """Per-connection `try`, not one around the loop: a single failure that
    aborted the whole publish would silently stall every other player, and
    §8.4's sequencing would then make them all resync."""
    hub, (bad, good) = hub_with("p1", "p2")
    broadcaster = WsBroadcaster(hub, media_base="/media")

    original = broadcaster._update

    def explode_for_bad(connection, *args, **kwargs):  # type: ignore[no-untyped-def]
        if connection is bad:
            raise RuntimeError("only this one")
        return original(connection, *args, **kwargs)

    broadcaster._update = explode_for_bad  # type: ignore[assignment]
    broadcaster.publish(GameId("g1"), 7, playing_state(), ())
    assert bad.close_code == 1011
    assert good.close_code is None
    assert len(parsed(good)) == 1


def test_a_slow_subscriber_is_closed_with_4408_and_the_game_survives() -> None:
    hub, (slow,) = hub_with("p1")
    slow._outbound._maxsize = 1  # type: ignore[attr-defined]
    broadcaster = WsBroadcaster(hub, media_base="/media")
    for _ in range(5):
        broadcaster.publish(GameId("g1"), 7, playing_state(), ())
    assert slow.close_code == 4408


def test_the_broadcaster_answers_the_two_subscriber_control_questions() -> None:
    hub, (one,) = hub_with("p1")
    broadcaster = WsBroadcaster(hub, media_base="/media")
    assert broadcaster.subscriber_count(GameId("g1")) == 1
    broadcaster.close_game_subscribers(GameId("g1"), 1001)
    assert one.close_code == 1001
    assert broadcaster.subscriber_count(GameId("g1")) == 0


def test_a_snapshot_is_sent_to_one_connection_only() -> None:
    hub, (one, two) = hub_with("p1", "p2")
    WsBroadcaster(hub, media_base="/media").snapshot_to(one, GameId("g1"), playing_state())
    assert parsed(one)[0]["type"] == "game.snapshot"
    assert parsed(two) == []


def test_a_rejected_command_comes_back_as_an_error_frame_not_a_future() -> None:
    """§8.2: the WS handler does not await a future — an unobserved
    `asyncio.Future` either logs "exception was never retrieved" or
    silently swallows the rejection."""
    connection = a_connection(FakeSocket())
    WsOrigin(connection, "cmd-1").resolve_rejected(RejectCode.NOT_ADJACENT, "'r7' is not adjacent")
    assert parsed(connection)[0] == {
        "type": "error",
        "command_id": "cmd-1",
        "code": "not_adjacent",
        "message": "'r7' is not adjacent",
    }


def test_a_transport_failure_comes_back_with_its_runtime_code() -> None:
    connection = a_connection(FakeSocket())
    WsOrigin(connection, "cmd-1").resolve_failed(RuntimeCode.GAME_RECOVERING, "recovering")
    assert parsed(connection)[0]["code"] == "game_recovering"


def test_success_and_ignore_send_nothing() -> None:
    """Success reaches the client as the broadcast every subscriber gets;
    an ignore is a benign race delivered to nobody (Spec 1 §11.1)."""
    connection = a_connection(FakeSocket())
    origin = WsOrigin(connection, "cmd-1")
    origin.resolve_ok(())
    origin.resolve_noop()
    assert parsed(connection) == []


def test_an_origin_on_a_closed_connection_does_not_raise() -> None:
    """Every `Origin` method is non-throwing and idempotent (`ports.py`): a
    delivery failure on a dead socket must never reach fault handling."""
    connection = a_connection(FakeSocket())
    connection.close(4408)
    WsOrigin(connection, "cmd-1").resolve_rejected(RejectCode.GAME_FULL, "full")


def test_the_origin_satisfies_the_port() -> None:
    from triviador.services.ports import Origin

    origin: Origin = WsOrigin(a_connection(FakeSocket()), "cmd-1")
    assert origin is not None
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd backend && uv run pytest tests/api/test_broadcaster.py -v --no-cov`
Expected: FAIL — `triviador.api.ws.broadcaster` does not exist.

- [ ] **Step 3: Write the origin**

`backend/src/triviador/api/ws/origins.py`:

```python
"""`WsOrigin`: how a socket command's outcome gets back to its sender.

§8.2 is explicit that the WebSocket handler must **not** await a future, so
this origin holds none. It puts an `error` frame on the connection's own
bounded outbound queue and returns. Success needs no frame at all: it
arrives as the `game.update` every subscriber receives, and a second
per-sender acknowledgement would restate a fact the client already has.
"""

from collections.abc import Sequence

from triviador.api.errors import ApiErrorCode
from triviador.api.schemas.ws import ErrorMessage
from triviador.api.ws.hub import Connection
from triviador.domain.game.actions import RejectCode
from triviador.domain.game.events import GameEvent
from triviador.services.ports import RuntimeCode


class WsOrigin:
    """Implements `services.ports.Origin`.

    Every method is non-throwing: `Connection.send` turns a full queue into
    a 4408 close rather than raising, so a delivery failure can never reach
    the runtime's fault handling (§5.2).
    """

    def __init__(self, connection: Connection, command_id: str) -> None:
        self._connection = connection
        self._command_id = command_id

    def resolve_ok(self, events: Sequence[GameEvent]) -> None:
        return None

    def resolve_noop(self) -> None:
        # Spec 1 §11.1: an `ignore` is delivered to nobody. A stale window
        # or a duplicate is a benign race, and reporting it would invite a
        # retry that is exactly wrong.
        return None

    def resolve_rejected(self, code: RejectCode, message: str) -> None:
        self._send(code, message)

    def resolve_failed(self, code: RuntimeCode, message: str) -> None:
        # `RuntimeCode` and `ApiErrorCode` share these four values by
        # construction, and `test_envelope.py` asserts they still do.
        self._send(ApiErrorCode(code.value), message)

    def _send(self, code: ApiErrorCode | RejectCode, message: str) -> None:
        self._connection.send(
            ErrorMessage(command_id=self._command_id, code=code, message=message)
        )
```

- [ ] **Step 4: Write the broadcaster**

`backend/src/triviador/api/ws/broadcaster.py`:

```python
"""The two ports the runtime holds, implemented over the hub.

`publish` is where §8.7's per-viewer projection happens, and it is the one
function in this codebase that must never raise: `GameRuntime._publish`
calls it after a durable commit, and an exception escaping it would
quarantine a game whose state is correct (§5.5).
"""

import logging
from collections.abc import Sequence

from triviador.api.projection.events import project_event
from triviador.api.projection.snapshot import project_snapshot
from triviador.api.projection.viewer import viewer_for
from triviador.api.schemas.ws import (
    PresenceMessage,
    SnapshotMessage,
    UpdateMessage,
    game_topic,
)
from triviador.api.ws.hub import Connection, Hub
from triviador.domain.game.events import GameEvent
from triviador.domain.game.state import GameState
from triviador.domain.ids import GameId

logger = logging.getLogger(__name__)


class WsBroadcaster:
    """Implements `Broadcaster` and `GameSubscriberControl`."""

    def __init__(self, hub: Hub, *, media_base: str) -> None:
        self._hub = hub
        self._media_base = media_base

    def publish(
        self,
        game_id: GameId,
        base_seq: int,
        state: GameState,
        events: Sequence[GameEvent],
    ) -> None:
        for connection in tuple(self._hub.subscribers(game_topic(str(game_id)))):
            try:
                connection.send(self._update(connection, game_id, base_seq, state, events))
            except Exception:
                # §5.5: a projection or serialization failure closes *that*
                # subscriber with 1011. The `try` is inside the loop so one
                # broken connection cannot cost the others their update, and
                # nothing propagates — the caller is the consumer task, and
                # an exception here would read as a runtime fault.
                logger.exception("projection failed for connection %s", connection.id)
                connection.close(1011)

    def _update(
        self,
        connection: Connection,
        game_id: GameId,
        base_seq: int,
        state: GameState,
        events: Sequence[GameEvent],
    ) -> UpdateMessage:
        viewer = viewer_for(state, connection.principal)
        snapshot = project_snapshot(state, viewer, media_base=self._media_base)
        projected = tuple(
            client_event
            for client_event in (project_event(event, viewer) for event in events)
            if client_event is not None
        )
        return UpdateMessage(
            game_id=str(game_id),
            base_seq=base_seq,
            seq=state.seq,
            state=snapshot.state,
            events=projected,
        )

    def snapshot_to(self, connection: Connection, game_id: GameId, state: GameState) -> None:
        """§8.5's reconnect path and §8.1's subscribe: one full snapshot,
        never an event catch-up."""
        viewer = viewer_for(state, connection.principal)
        snapshot = project_snapshot(state, viewer, media_base=self._media_base)
        connection.send(
            SnapshotMessage(game_id=str(game_id), seq=snapshot.seq, state=snapshot.state)
        )

    def presence(self, game_id: GameId) -> None:
        """§8.3: deliberately not a domain event — no `seq`, not persisted,
        absent from replay."""
        message = PresenceMessage(
            game_id=str(game_id), connected=self._hub.players_in(str(game_id))
        )
        for connection in tuple(self._hub.subscribers(game_topic(str(game_id)))):
            connection.send(message)

    # --- GameSubscriberControl ---------------------------------------------

    def close_game_subscribers(self, game_id: GameId, code: int) -> None:
        self._hub.close_game_subscribers(str(game_id), code)

    def subscriber_count(self, game_id: GameId) -> int:
        return self._hub.subscriber_count(str(game_id))
```

- [ ] **Step 5: Run the tests**

Run: `cd backend && uv run pytest tests/api -v --no-cov && uv run mypy --strict`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add backend/src/triviador/api/ws backend/tests/api
git commit -m "feat(api): the broadcaster's per-viewer publish, and a future-free WS origin"
```

---

## Task 16: The `/ws` endpoint — handshake, subscription, and actor derivation

**Files:**
- Create: `backend/src/triviador/api/ws/endpoint.py`
- Modify: `backend/src/triviador/api/deps.py` (add `hub`, `broadcaster`, `manager`), `backend/src/triviador/api/app.py`, `backend/tests/api/conftest.py`
- Test: `backend/tests/api/test_ws_endpoint.py`

**Interfaces:**
- Consumes: `CLIENT_MESSAGE_ADAPTER`, `Hub`, `Connection`, `run_sender`, `WsBroadcaster`, `WsOrigin`, `GameManager`, `QueuedCommand`, `origin_allowed`, `token_digest`, the runtime error types.
- Produces: `WsSocket` (Protocol: `accept`, `receive_text`, `send_text`, `close`); `serve_connection(*, socket, deps, cookie_token, origin) -> None`; `router` carrying `@router.websocket("/ws")`.

- [ ] **Step 1: Extend the test fixtures**

In `backend/tests/api/conftest.py`, give the `deps` fixture the socket-side collaborators and one live game, and add a `replace_deps` helper:

```python
from dataclasses import replace as _replace

from tests.runtime.conftest import manager_with_resident
from tests.runtime.fakes import FakeClock as RuntimeFakeClock
from triviador.api.ws.broadcaster import WsBroadcaster
from triviador.api.ws.hub import Hub
from triviador.db.security import token_digest
from triviador.domain.ids import SessionId, UserId


def replace_deps(deps: AppDependencies, **overrides: object) -> AppDependencies:
    return _replace(deps, **overrides)  # type: ignore[arg-type]


@pytest_asyncio.fixture
async def deps(settings: Settings, users: FakeUsers) -> AppDependencies:
    """One signed-in user, `u1`, whose cookie value is the literal `"tok"`,
    and one live game they are a player in. Every socket test starts from
    "authenticated participant" and takes away whatever it is testing."""
    clock = FakeClock()
    sessions = FakeSessions(users)
    hasher = FakeHasher()
    await users.create(
        user_id=UserId("u1"), username="u1", password_hash=hasher.hash("correct horse"),
        display_name="U1", role=UserRole.PLAYER,
    )
    await sessions.create(
        session_id=SessionId("s1"), user_id=UserId("u1"),
        token_hash=token_digest("tok"),
        expires_at=clock.now() + timedelta(days=30),
    )
    hub = Hub()
    manager, _ = manager_with_resident(
        lobby_state({"u1": 0, "u2": 1}), RuntimeFakeClock(T0)
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
    )
```

`tests/api/test_auth.py` keeps working: it only ever reads `deps.invites`, `deps.users`, `deps.clock` and `deps.settings`, and now starts with one extra pre-existing user — which is why its registrations use the username `alice`.

- [ ] **Step 2: Write the failing test**

`backend/tests/api/test_ws_endpoint.py`. The endpoint is driven through the `WsSocket` seam rather than a real WebSocket: the read loop, the close codes and the actor derivation are pure protocol logic, and Task 21 exercises the real route end to end.

```python
"""§6.5 and §8.1: what a socket may do, and as whom."""

import json

import pytest
from starlette.websockets import WebSocketDisconnect

from tests.api.conftest import ORIGIN, replace_deps
from tests.conftest import lobby_state
from tests.runtime.conftest import manager_with_resident, queued_commands
from tests.runtime.fakes import FakeClock as RuntimeFakeClock
from tests.runtime.fakes import T0
from triviador.api.ws.endpoint import serve_connection
from triviador.domain.ids import PlayerId


class ScriptedSocket:
    """A fixed script of client frames, then a disconnect."""

    def __init__(self, *frames: object) -> None:
        self._frames = [f if isinstance(f, str) else json.dumps(f) for f in frames]
        self.sent: list[dict] = []
        self.accepted = False
        self.closed_with: int | None = None

    async def accept(self) -> None:
        self.accepted = True

    async def receive_text(self) -> str:
        if not self._frames:
            raise WebSocketDisconnect(1000)
        return self._frames.pop(0)

    async def send_text(self, text: str) -> None:
        self.sent.append(json.loads(text))

    async def close(self, code: int) -> None:
        self.closed_with = code

    def types(self) -> list[str]:
        return [m["type"] for m in self.sent]


async def serve(deps, socket, *, token: str | None = "tok", origin: str | None = ORIGIN) -> None:
    await serve_connection(socket=socket, deps=deps, cookie_token=token, origin=origin)


def foreign_game(deps):
    """The same manager wiring, holding a game `u1` is not in."""
    manager, _ = manager_with_resident(lobby_state({"someone": 0}), RuntimeFakeClock(T0))
    return replace_deps(deps, manager=manager)


async def test_a_foreign_origin_is_refused_with_4403(deps) -> None:
    """§6.4. The socket is accepted first and then closed: a handshake
    refused before `accept` cannot carry a close code, and §11.1 gives the
    client a distinct reaction per code — which it can only read if it
    arrives."""
    socket = ScriptedSocket()
    await serve(deps, socket, origin="http://evil.lan")
    assert socket.accepted and socket.closed_with == 4403
    assert socket.sent == []


async def test_a_missing_or_dead_session_is_refused_with_4401(deps) -> None:
    socket = ScriptedSocket()
    await serve(deps, socket, token=None)
    assert socket.closed_with == 4401

    revoked = ScriptedSocket()
    await deps.sessions.revoke("s1", at=deps.clock.now())
    await serve(deps, revoked, token="tok")
    assert revoked.closed_with == 4401


async def test_an_authenticated_socket_is_greeted_with_the_server_time(deps) -> None:
    """§8.6: `hello` carries `server_time`; the client refines the offset
    from ping/pong afterwards, because a snapshot timestamp would embed
    one-way network delay."""
    socket = ScriptedSocket()
    await serve(deps, socket)
    assert socket.types() == ["hello"]
    assert socket.sent[0]["server_time"].startswith("2026-")


async def test_ping_is_answered_with_pong(deps) -> None:
    socket = ScriptedSocket({"type": "ping"})
    await serve(deps, socket)
    assert socket.types() == ["hello", "pong"]


async def test_subscribing_to_a_game_yields_a_snapshot_and_presence(deps) -> None:
    socket = ScriptedSocket({"type": "subscribe", "topic": "game:g1"})
    await serve(deps, socket)
    assert socket.types() == ["hello", "game.snapshot", "game.presence"]
    assert socket.sent[1]["state"]["you"]["player_id"] == "u1"


async def test_subscribing_to_someone_elses_game_closes_with_4403(deps) -> None:
    """§8.1: "Every `subscribe` performs its own authorization. Socket-level
    authentication is not sufficient." In Spec 1 that means participation."""
    socket = ScriptedSocket({"type": "subscribe", "topic": "game:g1"})
    await serve(foreign_game(deps), socket)
    assert socket.closed_with == 4403


async def test_resync_re_sends_the_snapshot_without_re_announcing_presence(deps) -> None:
    """§8.5: a reconnect renders from scratch. Presence has not changed, so
    re-broadcasting it would flicker every other client's roster."""
    socket = ScriptedSocket(
        {"type": "subscribe", "topic": "game:g1"}, {"type": "resync", "topic": "game:g1"}
    )
    await serve(deps, socket)
    assert socket.types().count("game.snapshot") == 2
    assert socket.types().count("game.presence") == 1


async def test_unsubscribing_stops_the_connection_counting_as_a_subscriber(deps) -> None:
    socket = ScriptedSocket(
        {"type": "subscribe", "topic": "game:g1"}, {"type": "unsubscribe", "topic": "game:g1"}
    )
    await serve(deps, socket)
    assert deps.hub.subscriber_count("g1") == 0


@pytest.mark.parametrize(
    "frame",
    ["{not json", {"type": "nonsense"}, {"type": "subscribe", "topic": "admin:games"}],
    ids=["not-json", "unknown-type", "spec-2-topic"],
)
async def test_a_malformed_frame_is_an_error_frame_not_a_close(deps, frame) -> None:
    """A parse failure is the client's bug, not a reason to drop a socket
    carrying a live game — the player would lose their open window over a
    typo in one frame."""
    socket = ScriptedSocket(frame)
    await serve(deps, socket)
    assert socket.types() == ["hello", "error"]
    assert socket.sent[1]["code"] == "validation_failed"
    assert socket.closed_with is None


async def test_a_frame_carrying_an_actor_is_refused_as_validation(deps) -> None:
    """The first of §11's two separate properties: the field is
    unacceptable, and strictness rejects it before any actor is derived."""
    socket = ScriptedSocket(
        {"type": "surrender", "command_id": "c1", "game_id": "g1", "actor_id": "u2"}
    )
    await serve(deps, socket)
    assert socket.sent[1]["code"] == "validation_failed"


async def test_a_command_is_built_with_the_sessions_identity(deps) -> None:
    """The second property. Asserted against the command that actually
    reached the queue — a successful command has no response to inspect."""
    runtime = deps.manager.live_runtimes()[0]
    socket = ScriptedSocket({"type": "surrender", "command_id": "c1", "game_id": "g1"})
    await serve(deps, socket)
    (queued,) = [q for q in queued_commands(runtime) if not q.stop]
    assert queued.command.actor_id == PlayerId("u1")


async def test_an_answer_frame_becomes_the_domain_command(deps) -> None:
    from decimal import Decimal

    from triviador.domain.game.actions import SubmitAnswer
    from triviador.domain.game.state import NumericAnswer

    runtime = deps.manager.live_runtimes()[0]
    socket = ScriptedSocket(
        {"type": "submit_answer", "command_id": "c1", "game_id": "g1", "deadline_id": 3,
         "payload": {"kind": "numeric", "value": "42.5"}}
    )
    await serve(deps, socket)
    (queued,) = [q for q in queued_commands(runtime) if not q.stop]
    assert queued.command == SubmitAnswer(PlayerId("u1"), 3, NumericAnswer(Decimal("42.5")))


async def test_a_command_for_a_game_the_sender_is_not_in_is_refused(deps) -> None:
    """Membership is re-checked per command, never inherited from having
    subscribed to something once."""
    socket = ScriptedSocket({"type": "surrender", "command_id": "c1", "game_id": "g1"})
    await serve(foreign_game(deps), socket)
    assert socket.sent[1]["code"] == "forbidden"
    assert socket.sent[1]["command_id"] == "c1"


async def test_an_unexpected_failure_never_echoes_its_exception_text(deps) -> None:
    """§6.3's sanitization rule, on the socket. The exception a broken
    loader raises carries a connection string; the frame the client sees
    must not."""

    class ExplodingManager:
        async def get(self, game_id):  # type: ignore[no-untyped-def]
            raise RuntimeError("connect to postgres://user:hunter2@db failed")

    socket = ScriptedSocket({"type": "surrender", "command_id": "c1", "game_id": "g1"})
    await serve(replace_deps(deps, manager=ExplodingManager()), socket)
    assert socket.sent[1]["code"] == "internal_error"
    assert socket.sent[1]["message"] == "internal error"
    assert "hunter2" not in json.dumps(socket.sent)


async def test_a_runtime_that_cannot_take_the_command_answers_without_closing(deps) -> None:
    """`submit` rejects rather than blocking, because its caller is this
    read loop — and a stalled read loop stops the client's heartbeat too."""
    deps.manager.live_runtimes()[0].closed = True
    socket = ScriptedSocket({"type": "surrender", "command_id": "c1", "game_id": "g1"})
    await serve(deps, socket)
    assert socket.sent[1]["code"] in {"server_busy", "game_recovering"}
    assert socket.closed_with is None


async def test_a_silent_socket_is_closed_rather_than_held_forever(deps) -> None:
    """§8.6's other half. The client pings every 15 s; a socket that has
    said nothing for 30 s is gone, and TCP will not tell us — a closed
    laptop lid or a Wi-Fi handover leaves the connection half-open, and
    with it a sender task and a name in every roster."""
    import asyncio

    class SilentSocket(ScriptedSocket):
        async def receive_text(self) -> str:
            await asyncio.sleep(3600)  # never speaks; the timeout must fire
            raise AssertionError("unreachable")

    socket = SilentSocket()
    # `Settings` is a Pydantic model, not a dataclass — `model_copy`, not
    # `dataclasses.replace`.
    quick = replace_deps(
        deps, settings=deps.settings.model_copy(update={"ws_idle_timeout_s": 0.01})
    )
    await asyncio.wait_for(serve(quick, socket), timeout=2)
    assert socket.closed_with == 1001


async def test_a_disconnect_removes_the_connection_and_its_subscriptions(deps) -> None:
    socket = ScriptedSocket({"type": "subscribe", "topic": "game:g1"})
    await serve(deps, socket)
    assert deps.hub.connections == {}
    assert deps.hub.subscriber_count("g1") == 0


async def test_a_second_tab_of_the_same_user_is_its_own_connection(deps) -> None:
    """§8.1 is one socket per browser tab, and §8.6's presence is per
    person — two connections, one name in the roster."""
    first = ScriptedSocket({"type": "subscribe", "topic": "game:g1"}, {"type": "ping"})
    second = ScriptedSocket({"type": "subscribe", "topic": "game:g1"})
    import asyncio

    await asyncio.gather(serve(deps, first), serve(deps, second))
    presence = [m for m in first.sent + second.sent if m["type"] == "game.presence"]
    assert all(m["connected"] == ["u1"] for m in presence)
```

- [ ] **Step 3: Run to verify it fails**

Run: `cd backend && uv run pytest tests/api/test_ws_endpoint.py -v --no-cov`
Expected: FAIL — `triviador.api.ws.endpoint` does not exist.

- [ ] **Step 4: Write the endpoint**

`backend/src/triviador/api/ws/endpoint.py`:

```python
"""One authenticated, multiplexed socket per browser tab (§8.1).

Three rules this file exists to enforce, in this order:

1. **The handshake is checked before anything else** — origin, then
   session. Both refusals are close codes rather than statuses, and both
   are sent *after* `accept`: an unaccepted handshake cannot carry a code,
   and §11.1 gives the client a different reaction for each one.
2. **Every `subscribe` re-authorizes.** Socket authentication is not
   sufficient; in Spec 1 a user may subscribe only to a game they play in.
3. **The actor is the principal.** A frame cannot even mention one
   (`extra="forbid"`), and the domain command is constructed here from
   `principal.user_id`.
"""

import asyncio
import logging
import uuid
from decimal import Decimal
from typing import Protocol

from fastapi import APIRouter, WebSocket
from pydantic import ValidationError
from starlette.websockets import WebSocketDisconnect

from triviador.api.deps import AppDependencies, deps_of
from triviador.api.errors import ApiErrorCode
from triviador.api.middleware import origin_allowed
from triviador.api.schemas.ws import (
    CLIENT_MESSAGE_ADAPTER,
    ClientMessage,
    ErrorMessage,
    HelloMessage,
    PickRegionFrame,
    PingFrame,
    PongMessage,
    ResyncFrame,
    SelectTargetFrame,
    SubmitAnswerFrame,
    SubscribeFrame,
    SurrenderFrame,
    UnsubscribeFrame,
)
from triviador.api.ws.hub import Connection, run_sender
from triviador.api.ws.origins import WsOrigin
from triviador.db.security import token_digest
from triviador.domain.game.actions import (
    Command,
    PickRegion,
    SelectAttackTarget,
    SubmitAnswer,
    Surrender,
)
from triviador.domain.game.state import ChoiceAnswer, NumericAnswer
from triviador.domain.ids import DeadlineId, GameId, PlayerId, RegionId
from triviador.runtime.errors import (
    GameRecovering,
    GameUnrecoverable,
    RuntimeClosed,
    ServerBusy,
    ServerRestarting,
)
from triviador.runtime.runtime import GameRuntime, QueuedCommand

logger = logging.getLogger(__name__)
router = APIRouter()

_FAILURE_CODES: tuple[tuple[type[Exception], ApiErrorCode], ...] = (
    (ServerBusy, ApiErrorCode.SERVER_BUSY),
    (RuntimeClosed, ApiErrorCode.SERVER_BUSY),
    (ServerRestarting, ApiErrorCode.SERVER_RESTARTING),
    (GameRecovering, ApiErrorCode.GAME_RECOVERING),
    (GameUnrecoverable, ApiErrorCode.GAME_UNRECOVERABLE),
)


class WsSocket(Protocol):
    async def accept(self) -> None: ...
    async def receive_text(self) -> str: ...
    async def send_text(self, text: str) -> None: ...
    async def close(self, code: int) -> None: ...


@router.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket) -> None:
    deps = deps_of(websocket)  # reads `app.state`, exactly as a request does
    await serve_connection(
        socket=websocket,
        deps=deps,
        cookie_token=websocket.cookies.get(deps.settings.session_cookie_name),
        origin=websocket.headers.get("origin"),
    )


async def serve_connection(
    *, socket: WsSocket, deps: AppDependencies, cookie_token: str | None, origin: str | None
) -> None:
    await socket.accept()

    if not origin_allowed(origin or "", deps.settings.allowed_origins):
        await socket.close(4403)
        return

    principal = (
        None
        if not cookie_token
        else await deps.sessions.resolve(token_digest(cookie_token), now=deps.clock.now())
    )
    if principal is None:
        await socket.close(4401)
        return

    connection = Connection(
        id=uuid.uuid4().hex,
        principal=principal,
        socket=socket,
        queue_size=deps.settings.ws_outbound_queue_size,
    )
    deps.hub.add(connection)
    sender = asyncio.create_task(run_sender(connection), name=f"ws-sender:{connection.id}")
    connection.send(HelloMessage(server_time=deps.clock.now()))
    try:
        await _read_loop(connection, deps)
    except WebSocketDisconnect:
        pass
    finally:
        subscribed_games = [
            t.removeprefix("game:") for t in connection.topics if t.startswith("game:")
        ]
        deps.hub.remove(connection)
        # One close path, unconditionally. Closing a socket the client has
        # already hung up on is safe: Starlette raises, and `run_sender`'s
        # own `except Exception` logs it and ends. A special case here for
        # "the client is gone" would have to cancel the sender, and a
        # cancel can land inside an in-flight write.
        connection.close(connection.close_code or 1000)
        for game_id in subscribed_games:
            # After removal, so the departing tab is already absent from the
            # roster everyone else receives.
            deps.broadcaster.presence(GameId(game_id))
        await sender


async def _read_loop(connection: Connection, deps: AppDependencies) -> None:
    socket: WsSocket = connection.socket  # type: ignore[assignment]
    while connection.close_code is None:
        try:
            # §8.6's server half. A read that never returns is the normal
            # shape of a half-open socket, so the loop is bounded rather
            # than trusting the transport to notice.
            raw = await asyncio.wait_for(
                socket.receive_text(), timeout=deps.settings.ws_idle_timeout_s
            )
        except TimeoutError:
            connection.close(1001)
            return
        try:
            frame = CLIENT_MESSAGE_ADAPTER.validate_json(raw)
        except ValidationError:
            _error(connection, None, ApiErrorCode.VALIDATION_FAILED, "frame failed validation")
            continue
        await _dispatch(connection, deps, frame)


async def _dispatch(connection: Connection, deps: AppDependencies, frame: ClientMessage) -> None:
    match frame:
        case PingFrame():
            connection.send(PongMessage(server_time=deps.clock.now()))
        case SubscribeFrame(topic=topic):
            await _subscribe(connection, deps, topic, resync=False)
        case ResyncFrame(topic=topic):
            await _subscribe(connection, deps, topic, resync=True)
        case UnsubscribeFrame(topic=topic):
            deps.hub.unsubscribe(connection, topic)
        case _:
            await _command(connection, deps, frame)


async def _subscribe(
    connection: Connection, deps: AppDependencies, topic: str, *, resync: bool
) -> None:
    if topic == "lobby":
        deps.hub.subscribe(connection, topic)
        connection.send(await deps.lobby_message("lobby.snapshot"))
        return

    game_id = GameId(topic.removeprefix("game:"))
    runtime = await _runtime_or_none(connection, deps, game_id)
    if runtime is None:
        return
    if PlayerId(connection.principal.user_id) not in runtime.state.players:
        # §8.1: every subscribe authorizes for itself. The whole connection
        # closes rather than the subscription being silently dropped —
        # `4403` is a code §11.1 gives the client an explicit reaction for,
        # and there is no per-topic error channel that would carry it.
        connection.close(4403)
        return

    if not resync:
        deps.hub.subscribe(connection, topic)
    deps.broadcaster.snapshot_to(connection, game_id, runtime.state)
    if not resync:
        deps.broadcaster.presence(game_id)


async def _command(connection: Connection, deps: AppDependencies, frame: ClientMessage) -> None:
    game_id = GameId(frame.game_id)  # type: ignore[union-attr]
    command_id: str = frame.command_id  # type: ignore[union-attr]
    runtime = await _runtime_or_none(connection, deps, game_id, command_id=command_id)
    if runtime is None:
        return

    actor = PlayerId(connection.principal.user_id)
    if actor not in runtime.state.players:
        _error(connection, command_id, ApiErrorCode.FORBIDDEN, "not a participant in that game")
        return

    try:
        runtime.submit(
            QueuedCommand(
                command=_to_command(frame, actor),
                # Unique per (connection, command_id): the operation id is
                # the idempotency key an ambiguous commit reconciles on
                # (§5.5), so two tabs reusing the same client-side id must
                # not collide.
                operation_id=f"{connection.id}:{command_id}",
                origin=WsOrigin(connection, command_id),
            )
        )
    except Exception as exc:
        _error(connection, command_id, *_failure(exc))


def _to_command(frame: ClientMessage, actor: PlayerId) -> Command:
    """Where the actor comes from. §6.5: "the hub constructs the domain
    command with `actor_id = principal.user_id`"."""
    match frame:
        case SubmitAnswerFrame(deadline_id=deadline_id, payload=payload):
            value = (
                ChoiceAnswer(payload.idx)
                if payload.kind == "choice"
                else NumericAnswer(Decimal(payload.value))
            )
            return SubmitAnswer(actor, DeadlineId(deadline_id), value)
        case PickRegionFrame(deadline_id=deadline_id, payload=payload):
            return PickRegion(actor, DeadlineId(deadline_id), RegionId(payload.region_id))
        case SelectTargetFrame(deadline_id=deadline_id, payload=payload):
            return SelectAttackTarget(actor, DeadlineId(deadline_id), RegionId(payload.region_id))
        case SurrenderFrame():
            return Surrender(actor)
        case _:
            raise AssertionError(f"not a command frame: {frame!r}")


async def _runtime_or_none(
    connection: Connection,
    deps: AppDependencies,
    game_id: GameId,
    *,
    command_id: str | None = None,
) -> GameRuntime | None:
    try:
        return await deps.manager.get(game_id)
    except Exception as exc:
        _error(connection, command_id, *_failure(exc))
        return None


def _failure(exc: Exception) -> tuple[ApiErrorCode, str]:
    """The code *and* the message, decided together.

    Returning only the code and letting each caller pass `str(exc)` was a
    leak: the unexpected branch is reached by a loader or driver exception,
    whose text routinely carries a connection string or a fragment of SQL.
    That is precisely what §6.3 stops a 500 body from doing, and a socket
    frame is no less visible to the client than a response body.

    The five known conditions keep their message: each is one of our own
    exception types raised with a message written for a client to read.
    """
    for exc_type, code in _FAILURE_CODES:
        if isinstance(exc, exc_type):
            return code, str(exc)
    logger.exception("unexpected failure serving a socket frame")
    return ApiErrorCode.INTERNAL_ERROR, "internal error"


def _error(
    connection: Connection, command_id: str | None, code: ApiErrorCode, message: str
) -> None:
    connection.send(ErrorMessage(command_id=command_id, code=code, message=message))
```

A note on `_FAILURE_CODES` being a tuple rather than a dict: `RuntimeClosed` and `ServerBusy` are unrelated classes, but `isinstance` order matters as soon as one of these gains a subclass, and an ordered sequence makes the precedence visible.

- [ ] **Step 5: Extend `AppDependencies` and mount the router**

Add to the dataclass:

```python
    hub: "Hub"
    broadcaster: "WsBroadcaster"
    manager: "GameManager"

    async def lobby_message(self, kind: str) -> "LobbyMessage":
        """Overridden in Task 18, when there is a catalog to read. Until
        then an empty lobby is honest: nothing can create a game yet."""
        return LobbyMessage(type=kind, games=())
```

and `app.include_router(endpoint.router)` in `create_app`.

- [ ] **Step 6: Run the tests**

Run: `cd backend && uv run pytest tests/api -v --no-cov && uv run mypy --strict`
Expected: PASS

- [ ] **Step 7: Commit**

```bash
git add backend/src/triviador/api backend/tests/api
git commit -m "feat(api): the /ws endpoint — handshake codes, per-subscribe authz, actor from the session"
```

---

## Task 17: The composition root, the lifespan, health, and maps

**Files:**
- Create: `backend/src/triviador/api/http/health.py`, `backend/src/triviador/api/http/maps.py`, `backend/src/triviador/api/schemas/maps.py`
- Modify: `backend/src/triviador/api/app.py`, `backend/src/triviador/api/deps.py`, `backend/src/triviador/config.py`
- Test: `backend/tests/api/test_health.py`, `backend/tests/api/test_maps.py`

**Interfaces:**
- Consumes: `create_engine`, `sessionmaker_for`, `UnitOfWork`, `GameRepository`, `QuestionBank`-backed `Materialiser`, `GameLoader`, `GameManager`, `Watchdog`, `Reaper`, `SystemClock`, `MapRegistry`, `Argon2Hasher`, the four repositories, `startup_problems`, `configure_logging`.
- Produces: `build_dependencies(settings) -> BuiltApp` (deps + engine + watchdog + reaper); `build_app(settings) -> FastAPI` with the lifespan attached; `Readiness` (a mutable record on `app.state`); routes `GET /api/health/live`, `GET /api/health/ready`, `GET /api/maps`, `GET /api/maps/{map_id}`; schemas `MapSummary`, `MapDetail`, `MapRegion`; setting `maps_public_base`.

- [ ] **Step 1: Write the failing tests**

`backend/tests/api/test_health.py`:

```python
"""§10.6. Two probes with deliberately different dependencies.

A liveness probe that touches the database restarts a healthy process
during a database blip — which is how a five-second outage becomes a
five-minute one.
"""

import httpx

from triviador.api.deps import Readiness


async def test_liveness_is_true_before_anything_is_ready(client: httpx.AsyncClient) -> None:
    response = await client.get("/api/health/live")
    assert response.status_code == 200
    assert response.json() == {"status": "alive"}


async def test_liveness_never_touches_the_database(client: httpx.AsyncClient, deps) -> None:
    deps.database.reachable = False
    assert (await client.get("/api/health/live")).status_code == 200
    assert deps.database.pings == 0


async def test_readiness_is_503_until_startup_recovery_finishes(
    client: httpx.AsyncClient, deps
) -> None:
    """§10.5's order: migrate, then recover, then serve. Reporting ready
    before recovery has finished means a load balancer sends a player to a
    process whose games have no owner and no timer."""
    deps.readiness.recovery_complete = False
    response = await client.get("/api/health/ready")
    assert response.status_code == 503
    assert response.json()["details"]["recovery_complete"] is False


async def test_a_ready_process_reports_each_check(client: httpx.AsyncClient) -> None:
    response = await client.get("/api/health/ready")
    assert response.status_code == 200
    body = response.json()
    assert body["database"] is True
    assert body["migrations_current"] is True
    assert body["recovery_complete"] is True
    assert body["degraded_games"] == []


async def test_a_failed_game_is_reported_as_a_degraded_detail_without_failing_readiness(
    client: httpx.AsyncClient, deps
) -> None:
    """§5.6: `Failed` is cleared only by operator action. One unrecoverable
    game must be visible, but it must not take the whole process out of
    rotation — the other games are fine and there is nowhere else to send
    their players (ADR-002: one application process)."""
    from triviador.domain.ids import GameId
    from triviador.runtime.manager import Failed

    deps.manager._entries[GameId("g9")] = Failed(reason="stream will never decode")
    response = await client.get("/api/health/ready")
    assert response.status_code == 200
    assert response.json()["degraded_games"] == [{"game_id": "g9", "reason": "stream will never decode"}]


async def test_readiness_reports_a_database_that_went_away_after_startup(
    client: httpx.AsyncClient, deps
) -> None:
    """The failure a startup-time flag cannot see: the process booted fine
    and PostgreSQL died an hour later. §10.6 asks for "database reachable",
    present tense, so the probe runs per request."""
    assert (await client.get("/api/health/ready")).status_code == 200
    deps.database.reachable = False
    response = await client.get("/api/health/ready")
    assert response.status_code == 503
    assert response.json()["database"] is False
```

`backend/tests/api/test_maps.py`:

```python
"""§6.1: `GET /api/maps/{id}` returns region ids, display names, and
`svg_url` — **never** adjacency."""

import httpx


async def test_listing_maps_requires_a_session(client: httpx.AsyncClient) -> None:
    assert (await client.get("/api/maps")).status_code == 401


async def test_the_registry_is_listed(signed_in: httpx.AsyncClient) -> None:
    response = await signed_in.get("/api/maps")
    assert response.status_code == 200
    assert [m["map_id"] for m in response.json()] == ["grid"]
    assert response.json()[0]["region_count"] == 9


async def test_a_map_carries_its_regions_and_an_svg_url(signed_in: httpx.AsyncClient) -> None:
    response = await signed_in.get("/api/maps/grid")
    body = response.json()
    assert body["svg_url"] == "/maps/grid/map.svg"
    assert {r["region_id"] for r in body["regions"]} == {f"r{i}" for i in range(9)}
    assert body["regions"][0]["display_name"] == "R0"


async def test_adjacency_is_never_returned(signed_in: httpx.AsyncClient) -> None:
    """§8.8's reason: the client is told its options, not the rule that
    produced them. Adjacency lives in `domain/maps` alone, and a client
    that had it would be holding a fragment of the ruleset that can drift."""
    text = (await signed_in.get("/api/maps/grid")).text
    assert "adjacency" not in text
    assert "neighbours" not in text


async def test_an_unknown_map_is_404_with_its_own_code(signed_in: httpx.AsyncClient) -> None:
    response = await signed_in.get("/api/maps/atlantis")
    assert response.status_code == 404
    assert response.json()["code"] == "map_unknown"
```

Add a `signed_in` fixture to `tests/api/conftest.py`: the `client` with the session cookie for `u1` already set (`client.cookies.set(settings.session_cookie_name, "tok")`), and a `map_root` fixture reusing `tests/runtime/integration/conftest.write_grid_map` into `tmp_path` so `deps.maps` is a real `MapRegistry` over a real file.

- [ ] **Step 2: Run to verify they fail**

Run: `cd backend && uv run pytest tests/api/test_health.py tests/api/test_maps.py -v --no-cov`
Expected: FAIL — neither router exists and `Readiness` is undefined.

- [ ] **Step 3: Add `Readiness`, the map settings, and the schemas**

In `config.py`, one more setting:

```python
    # Where Caddy serves `data/maps/<id>/map.svg` from (§10.2). The API
    # names the URL; it never serves the bytes.
    maps_public_base: str = "/maps"
```

In `deps.py`:

```python
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
```

plus the probe the database check needs — declared in `services/ports.py` beside the others, so the contract suite can hand the app a fake that answers without a connection:

```python
class DatabaseProbe(Protocol):
    """Is PostgreSQL answering *right now*.

    A `bool` set at startup would report a database that has since gone
    away as reachable, and §10.6 asks readiness for "database reachable",
    present tense. Non-throwing: a probe that raised would reach the 503
    handler and answer with `database_unavailable` instead of the
    checklist a probe is asking for.
    """

    async def ping(self) -> bool: ...
```

with the adapter in `db/engine.py`:

```python
class EnginePing:
    def __init__(self, engine: AsyncEngine) -> None:
        self._engine = engine

    async def ping(self) -> bool:
        try:
            async with self._engine.connect() as connection:
                await connection.execute(text("SELECT 1"))
        except SQLAlchemyError:
            return False
        return True
```

Then add `readiness: Readiness`, `database: DatabaseProbe`, `games: GameCatalogPort`, `maps: MapProvider`, `presets: PresetPort` to `AppDependencies`.

`backend/src/triviador/api/schemas/maps.py`:

```python
from pydantic import BaseModel, ConfigDict


class MapSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    map_id: str
    region_count: int


class MapRegion(BaseModel):
    model_config = ConfigDict(extra="forbid")

    region_id: str
    display_name: str


class MapDetail(BaseModel):
    """No adjacency field, for the same structural reason `ClientQuestion`
    has no `is_correct`: a field that does not exist cannot be serialized
    by accident."""

    model_config = ConfigDict(extra="forbid")

    map_id: str
    svg_url: str
    regions: tuple[MapRegion, ...]
```

- [ ] **Step 4: Write the two routers**

`backend/src/triviador/api/http/maps.py`:

```python
from fastapi import APIRouter

from triviador.api.deps import Deps, Principal
from triviador.api.errors import ApiError, ApiErrorCode
from triviador.api.schemas.maps import MapDetail, MapRegion, MapSummary
from triviador.domain.ids import MapId
from triviador.maps.registry import InvalidMapError

router = APIRouter(prefix="/api/maps", tags=["maps"])


@router.get("")
async def list_maps(deps: Deps, principal: Principal) -> list[MapSummary]:
    summaries = []
    for map_id in deps.maps.available():
        loaded = deps.maps.load_with_digest(map_id)
        summaries.append(
            MapSummary(map_id=str(map_id), region_count=len(loaded.definition.regions))
        )
    return summaries


@router.get("/{map_id}")
async def get_map(map_id: str, deps: Deps, principal: Principal) -> MapDetail:
    try:
        loaded = deps.maps.load_with_digest(MapId(map_id))
    except InvalidMapError as exc:
        # An unregistered id and a corrupt `map.json` are both 404 to a
        # client: neither is a map it can play on, and the difference is an
        # operator's problem, visible in the log.
        raise ApiError(ApiErrorCode.MAP_UNKNOWN, 404, "no such map") from exc
    return MapDetail(
        map_id=str(loaded.definition.map_id),
        svg_url=f"{deps.settings.maps_public_base}/{map_id}/map.svg",
        regions=tuple(
            MapRegion(region_id=str(r.region_id), display_name=r.display_name)
            for r in loaded.definition.regions
        ),
    )
```

`backend/src/triviador/api/http/health.py`:

```python
"""§10.6's two probes.

`live` answers from the process alone. `ready` reports the recorded result
of the startup sequence plus the two things that can change while running:
the database, and any game the manager has given up on.
"""

from fastapi import APIRouter, Response
from pydantic import BaseModel, ConfigDict

from triviador.api.deps import Deps

router = APIRouter(prefix="/api/health", tags=["health"])


class DegradedGame(BaseModel):
    model_config = ConfigDict(extra="forbid")

    game_id: str
    reason: str


class ReadinessReport(BaseModel):
    """Named `ReadinessReport`, not `Readiness`: `deps.Readiness` is the
    mutable record the lifespan writes, and two same-named types one import
    apart is the kind of collision that gets resolved by whichever module
    was imported last."""

    model_config = ConfigDict(extra="forbid")

    database: bool
    migrations_current: bool
    recovery_complete: bool
    degraded_games: tuple[DegradedGame, ...]


@router.get("/live")
async def live() -> dict[str, str]:
    return {"status": "alive"}


@router.get("/ready")
async def ready(deps: Deps, response: Response) -> ReadinessReport:
    state = deps.readiness
    body = ReadinessReport(
        # Probed, not remembered: a flag set at startup reports a database
        # that died an hour ago as reachable, and readiness is the one
        # endpoint whose whole job is to notice.
        database=await deps.database.ping(),
        migrations_current=state.migrations_current,
        recovery_complete=state.recovery_complete,
        degraded_games=tuple(
            DegradedGame(game_id=str(gid), reason=reason) for gid, reason in deps.manager.degraded()
        ),
    )
    if not (body.database and body.migrations_current and body.recovery_complete):
        # A degraded game is deliberately *not* part of this condition:
        # §5.6 clears `Failed` only by operator action, and ADR-002 gives
        # the process no peer to fail over to, so taking the whole server
        # out of rotation over one game punishes every other player.
        response.status_code = 503
    return body
```

The 503 path returns the same `ReadinessReport` model rather than an error envelope, deliberately: a probe wants the checklist, not a code. It is the second and last documented exception to "every response is an envelope or a declared success model" — and it *is* a declared success model, so the rule holds.

- [ ] **Step 5: Write the composition root**

Extend `backend/src/triviador/api/app.py`:

```python
"""...and the composition root proper.

`create_app` (above) is handed its dependencies; `build_app` constructs
them. That is the split every contract test in `tests/api/` depends on —
a factory that reached for an engine could not be called without a
database.
"""

import asyncio
import random
import secrets
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from triviador.api.deps import AppDependencies, Readiness
from triviador.api.logging import configure_logging
from triviador.api.ws.broadcaster import WsBroadcaster
from triviador.api.ws.hub import Hub
from triviador.config import Settings, startup_problems
from triviador.db.engine import create_engine, sessionmaker_for
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
    maps = MapRegistry(root=settings.maps_root)
    uow = UnitOfWork(sessions)
    games = GameRepository(sessions)
    rng = random.Random()

    manager = GameManager(
        loader=GameLoader(uow=uow, maps=maps),
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
        hub=hub,
        broadcaster=broadcaster,
        manager=manager,
        readiness=Readiness(),
        games=games,
        maps=maps,
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
    app = create_app(built.deps, lifespan=_lifespan(built))
    return app


def _lifespan(built: BuiltApp):  # type: ignore[no-untyped-def]
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
            logger.error("startup recovery could not load %d game(s): %s",
                         len(unloadable), ", ".join(unloadable))
        logger.info("startup recovery complete", extra={"recovered":
                    len(deps.manager.live_runtimes())})
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
```

`_head_revision()` reads the head from Alembic's script directory (`ScriptDirectory.from_config(Config(...)).get_current_head()`); implement it beside `build_app` and point the `Config` at `backend/alembic.ini`. `create_app` grows a `lifespan=None` keyword forwarded to `FastAPI(...)`, so contract tests keep constructing an app with no lifespan at all.

`Watchdog(manager, clock, interval_s, grace_s)` and `Reaper(manager, games, subscribers, clock, interval_s, empty_lobby_grace_minutes, lobby_max_age_hours)` are Plan 4's signatures, all keyword-only; both expose `start()` and `aclose()` and neither has a `run()`. Re-read `runtime/watchdog.py` and `runtime/reaper.py` if any of this does not compile — those modules own the contract, not this plan.

- [ ] **Step 6: Run the tests**

Run: `cd backend && uv run pytest tests/api -v --no-cov && uv run mypy --strict`
Expected: PASS

- [ ] **Step 7: Commit**

```bash
git add backend/src/triviador backend/tests/api
git commit -m "feat(api): composition root, lifespan with startup recovery, health and maps"
```

---

## Task 18: The game endpoints, and §6.2's two commits

**Files:**
- Create: `backend/src/triviador/api/http/games.py`
- Modify: `backend/src/triviador/api/schemas/games.py`, `backend/src/triviador/api/deps.py`, `backend/src/triviador/api/app.py`
- Test: `backend/tests/api/test_games.py`

**Interfaces:**
- Consumes: `GameCatalogPort`, `PresetPort`, `MapProvider`, `GameManager`, `FutureOrigin`, `QueuedCommand`, `JoinGame`, `StartGame`, `project_snapshot`, `viewer_for`, `Accepted`/`Ignored`/`Rejected`/`Failed`.
- Produces: `CreateGameRequest`; `LobbyGameSummary`; `submit_and_wait(deps, game_id, command, operation_id) -> GameRuntime`; routes `GET /api/games`, `POST /api/games`, `GET /api/games/{id}`, `POST /api/games/{id}/join`, `POST /api/games/{id}/start`; `AppDependencies.lobby_message` reading the catalog.

- [ ] **Step 1: Write the failing test**

`backend/tests/api/test_games.py`. The suite runs against two more fakes, with the real `GameManager` from the `deps` fixture — the runtime path is real, only storage is not. Add both to `tests/api/fakes.py`:

```python
@dataclass
class FakeGameCatalog:
    """`GameCatalogPort`. `created` records the keyword arguments verbatim,
    so a test can assert on `map_sha256` and `preset_id` — the two fields
    that are wrong-but-plausible if creation is miswired, and that no
    later request would reveal."""

    created: list[dict[str, object]] = field(default_factory=list)
    summaries: dict[GameId, GameSummary] = field(default_factory=dict)

    async def create(self, **kwargs: object) -> None:
        self.created.append(kwargs)
        game_id = kwargs["game_id"]
        self.summaries[game_id] = GameSummary(  # type: ignore[index]
            game_id=game_id, map_id=kwargs["map_id"], host_id=kwargs["host_id"],
            status="lobby", max_players=3, player_count=1,
            created_at=T0,
        )

    async def get_summary(self, game_id: GameId) -> GameSummary | None:
        return self.summaries.get(game_id)

    async def list_joinable(self) -> tuple[GameSummary, ...]:
        return tuple(s for s in self.summaries.values() if s.status == "lobby")


@dataclass
class FakePresets:
    """`PresetPort`. Two presets: `default` (three players, `DEFAULT_RULES`)
    and `two-player`, which exists because a test that wants to assert
    *authorization* on `start` must not be blocked by
    `NOT_ENOUGH_PLAYERS`."""

    presets: dict[str, PresetRecord] = field(
        default_factory=lambda: {
            "default": PresetRecord("default", "Default", DEFAULT_RULES),
            "two-player": PresetRecord(
                "two-player", "Two", replace(DEFAULT_RULES, player_count=2,
                                             claims_by_rank=(2, 1)),
            ),
        }
    )

    async def get(self, preset_id: str) -> PresetRecord | None:
        return self.presets.get(preset_id)

    async def get_default(self) -> PresetRecord | None:
        return self.presets.get("default")
```

and extend `tests/api/conftest.py` with `games=FakeGameCatalog()`, `presets=FakePresets()`, `maps=MapRegistry(root=map_root)`, plus a `stranger_client` — a third signed-in user (`u3`) who never joins anything.

```python
"""§6.1 and §6.2. The important test is the two-commit one."""

import httpx
import pytest

from triviador.api.errors import ApiErrorCode
from triviador.domain.game.actions import RejectCode


async def create(client: httpx.AsyncClient, **body: object) -> httpx.Response:
    return await client.post("/api/games", json={"map_id": "grid", **body})


async def test_creating_a_game_returns_the_snapshot_with_the_host_already_seated(
    signed_in: httpx.AsyncClient,
) -> None:
    """§6.2: `tx1` writes the row and the genesis event; the host then joins
    *through the runtime*, because putting seat allocation on a second
    mutation path is what §8.2 forbids."""
    response = await create(signed_in)
    assert response.status_code == 201
    state = response.json()["state"]
    assert [p["player_id"] for p in state["players"]] == ["u1"]
    assert state["players"][0]["seat"] == 0
    assert response.json()["seq"] >= 2  # genesis at 1, PlayerJoined after it


async def test_the_created_row_records_the_maps_digest(
    signed_in: httpx.AsyncClient, deps
) -> None:
    """The pin recovery verifies before folding anything (Plan 4's loader).
    A creation path that wrote the wrong digest would make every one of its
    games unrecoverable after the first restart."""
    await create(signed_in)
    (created,) = deps.games.created
    assert created["map_sha256"] == deps.maps.load_with_digest("grid").sha256


async def test_creating_without_a_preset_uses_the_default(
    signed_in: httpx.AsyncClient, deps
) -> None:
    await create(signed_in)
    assert deps.games.created[0]["preset_id"] == "default"


async def test_an_unknown_preset_is_404(signed_in: httpx.AsyncClient) -> None:
    response = await create(signed_in, preset_id="nope")
    assert response.status_code == 404
    assert response.json()["code"] == ApiErrorCode.PRESET_UNKNOWN


async def test_no_default_preset_is_a_409_not_a_500(
    signed_in: httpx.AsyncClient, deps
) -> None:
    """§7 leaves "never zero" to application logic. When that logic has
    failed, the honest answer is a conflict naming the cause — not a
    `NoneType has no attribute rules` in a 500."""
    deps.presets.presets.clear()
    response = await create(signed_in)
    assert response.status_code == 409
    assert response.json()["code"] == ApiErrorCode.NO_DEFAULT_PRESET


async def test_an_unknown_map_is_404(signed_in: httpx.AsyncClient) -> None:
    response = await create(signed_in, map_id="atlantis")
    assert response.status_code == 404
    assert response.json()["code"] == ApiErrorCode.MAP_UNKNOWN


async def test_creating_requires_a_session(client: httpx.AsyncClient) -> None:
    assert (await create(client)).status_code == 401


async def test_the_open_lobby_list_comes_from_the_catalog(
    signed_in: httpx.AsyncClient, deps
) -> None:
    """§6.1: "`GET /api/games` **excludes zero-player lobbies**", which the
    repository's inner JOIN already does — so this route must not
    reimplement the filter, only render what it is given."""
    await create(signed_in)
    response = await signed_in.get("/api/games")
    assert [g["game_id"] for g in response.json()] == [deps.games.created[0]["game_id"]]


async def test_a_lobby_snapshot_is_readable_by_anyone_signed_in(
    signed_in: httpx.AsyncClient, other_client: httpx.AsyncClient
) -> None:
    """Reading a lobby is how a player decides whether to join it."""
    game_id = (await create(signed_in)).json()["state"]["game_id"]
    response = await other_client.get(f"/api/games/{game_id}")
    assert response.status_code == 200
    assert response.json()["state"]["you"]["player_id"] is None


async def test_a_started_games_snapshot_is_refused_to_a_non_participant(
    signed_in: httpx.AsyncClient, other_client: httpx.AsyncClient, deps
) -> None:
    """Spectating is Spec 2. A snapshot of a live game carries the open
    question, so serving it to a stranger would be exactly the leak §8.7
    spends its whole length preventing — and the lobby exemption above is
    safe precisely because a lobby has no question."""
    game_id = (await create(signed_in)).json()["state"]["game_id"]
    await _force_started(deps, game_id)
    assert (await other_client.get(f"/api/games/{game_id}")).status_code == 403


async def test_an_unknown_game_is_404(signed_in: httpx.AsyncClient) -> None:
    assert (await signed_in.get("/api/games/nope")).status_code == 404


async def test_joining_seats_the_second_player(
    signed_in: httpx.AsyncClient, other_client: httpx.AsyncClient
) -> None:
    game_id = (await create(signed_in)).json()["state"]["game_id"]
    response = await other_client.post(f"/api/games/{game_id}/join")
    assert response.status_code == 200
    seats = {p["player_id"]: p["seat"] for p in response.json()["state"]["players"]}
    assert seats == {"u1": 0, "u2": 1}


async def test_joining_twice_is_409_carrying_the_domains_own_code(
    signed_in: httpx.AsyncClient,
) -> None:
    """§6.3: `RejectedCommand → 409 + its RejectCode`, and the code is the
    envelope's `code` rather than a nested detail."""
    game_id = (await create(signed_in)).json()["state"]["game_id"]
    response = await signed_in.post(f"/api/games/{game_id}/join")
    assert response.status_code == 409
    assert response.json()["code"] == RejectCode.ALREADY_JOINED


async def test_starting_with_too_few_players_is_409(signed_in: httpx.AsyncClient) -> None:
    game_id = (await create(signed_in)).json()["state"]["game_id"]
    response = await signed_in.post(f"/api/games/{game_id}/start")
    assert response.status_code == 409
    assert response.json()["code"] == RejectCode.NOT_ENOUGH_PLAYERS


async def test_a_stranger_cannot_start_a_game(
    signed_in: httpx.AsyncClient, stranger_client: httpx.AsyncClient
) -> None:
    """Guard 3, reached through HTTP: `StartGame` carries an actor, and an
    actor who is not an active player is rejected.

    Note what this does **not** assert. Any seated player may start, not
    only the host — see the note below the test list.
    """
    game_id = (await create(signed_in)).json()["state"]["game_id"]
    response = await stranger_client.post(f"/api/games/{game_id}/start")
    assert response.status_code == 409
    assert response.json()["code"] == RejectCode.NOT_A_PARTICIPANT


async def test_any_seated_player_may_start(
    signed_in: httpx.AsyncClient, other_client: httpx.AsyncClient
) -> None:
    """The positive half of the same decision, asserted so that adding a
    host-only rule later has to change a test that says what it is
    changing."""
    game_id = (await create(signed_in, preset_id="two-player")).json()["state"]["game_id"]
    await other_client.post(f"/api/games/{game_id}/join")
    response = await other_client.post(f"/api/games/{game_id}/start")
    assert response.status_code == 200
    assert response.json()["state"]["phase"] != "lobby"


async def test_a_recovering_game_answers_503_rather_than_409(
    signed_in: httpx.AsyncClient, deps
) -> None:
    """§6.3 keeps `RuntimeCode` and `RejectCode` on different status codes
    for a reason: a 409 tells the client "do not send that again", and a
    recovering game is precisely the case where it should."""
    from datetime import UTC, datetime

    from triviador.domain.ids import GameId
    from triviador.runtime.manager import Recovering

    game_id = (await create(signed_in)).json()["state"]["game_id"]
    deps.manager._entries[GameId(game_id)] = Recovering(
        attempt=1, next_at=datetime(2026, 8, 18, tzinfo=UTC)
    )
    response = await signed_in.post(f"/api/games/{game_id}/join")
    assert response.status_code == 503
    assert response.json()["code"] == ApiErrorCode.GAME_RECOVERING


async def test_a_lobby_subscriber_is_told_when_a_game_appears(
    signed_in: httpx.AsyncClient, deps
) -> None:
    """The lobby list is a live view: a player sitting on `/` must see a
    new game without polling."""
    from tests.api.test_ws_hub import FakeSocket, a_connection, parsed

    watcher = a_connection(FakeSocket(), id="w", user_id="u2")
    deps.hub.add(watcher)
    deps.hub.subscribe(watcher, "lobby")
    await create(signed_in)
    assert [m["type"] for m in parsed(watcher)] == ["lobby.update"]
```

**Host-only start is not a rule this system has.** Nothing in Spec 1 or Spec 1B requires it: `_decide_start` validates the player count and the drawn pool, guard 3 validates that the actor is an active player, and `GameState` does not retain `host_id` at all — the host is a fact about the `games` row, not about the folded state. Enforcing it here would mean either reading the catalog on every start (a second authorization path outside the domain) or amending the domain to carry a host. Neither is warranted by a requirement nobody wrote down, so the two tests above pin the behaviour that actually exists. If host-only start is wanted later it is a domain amendment with its own `RejectCode`, not a check bolted onto a route.

`stranger_client` is a third signed-in user who never joins. `two-player` is a preset in `FakePresets` whose `player_count` is 2, so a two-player lobby can legitimately start — with `DEFAULT_RULES`' three, `test_any_seated_player_may_start` would be asserting `NOT_ENOUGH_PLAYERS` while claiming to assert authorization. `_force_started(deps, game_id)` replaces the resident runtime's state with a started one via `runtime.replace_state_for_test(...)` — the seam Plan 4 already provides — using `tests/runtime/conftest.warmup_state()`. Add `other_client` to the conftest: a second `httpx.AsyncClient` over the same app carrying `u2`'s cookie.

- [ ] **Step 2: Run to verify it fails**

Run: `cd backend && uv run pytest tests/api/test_games.py -v --no-cov`
Expected: FAIL — `triviador.api.http.games` does not exist.

- [ ] **Step 3: Write the router**

`backend/src/triviador/api/http/games.py`:

```python
"""§6.1's game surface, and §6.2's deliberate two commits.

Every mutation here goes through the one serialised queue. There is no
second route by which state can change (§8.2) — including creation, where
the `games` row and its genesis event are written directly *because there
is no runtime before the game exists*, and the host's `PlayerJoined` then
goes through the queue like everything else.
"""

import uuid

from fastapi import APIRouter

from triviador.api.deps import Deps, Principal
from triviador.api.errors import ApiError, ApiErrorCode
from triviador.api.projection.snapshot import project_snapshot
from triviador.api.projection.viewer import viewer_for
from triviador.api.schemas.games import CreateGameRequest, GameSnapshot, LobbyGameSummary
from triviador.api.schemas.ws import LobbyMessage, game_topic
from triviador.domain.game.actions import Command, JoinGame, RejectedCommand, StartGame
from triviador.domain.game.state import Phase
from triviador.domain.ids import GameId, MapId, PlayerId
from triviador.maps.registry import InvalidMapError
from triviador.runtime.origins import Accepted, Failed, FutureOrigin, Ignored, Rejected
from triviador.runtime.runtime import GameRuntime, QueuedCommand
from triviador.services.identity import AuthenticatedPrincipal

router = APIRouter(prefix="/api/games", tags=["games"])


async def _display_name(deps: Deps, principal: AuthenticatedPrincipal) -> str:
    user = await deps.users.get(principal.user_id)
    if user is None:
        raise ApiError(ApiErrorCode.UNAUTHENTICATED, 401, "not signed in")
    return user.display_name


def _snapshot(deps: Deps, runtime: GameRuntime, principal: AuthenticatedPrincipal) -> GameSnapshot:
    state = runtime.state
    return project_snapshot(
        state, viewer_for(state, principal), media_base=deps.settings.media_public_base
    )


async def _submit(deps: Deps, game_id: GameId, command: Command) -> GameRuntime:
    """One command, awaited to its outcome.

    REST genuinely awaits its result (§8.2), so this is the one origin in
    the system that holds a future. A cancelled request leaves that future
    settled by nobody — `FutureOrigin` already treats an `InvalidStateError`
    as a logged non-event, because the batch is durable and destroying a
    healthy game over a dead HTTP connection would be the actual bug.
    """
    runtime = await deps.manager.get(game_id)
    origin = FutureOrigin()
    runtime.submit(
        QueuedCommand(command=command, operation_id=uuid.uuid4().hex, origin=origin)
    )
    outcome = await origin.future
    match outcome:
        case Accepted() | Ignored():
            return runtime
        case Rejected(code=code, message=message):
            raise RejectedCommand(code, message)
        case Failed(code=code, message=message):
            raise ApiError(ApiErrorCode(code.value), 503, message)


async def _publish_lobby(deps: Deps) -> None:
    message = await deps.lobby_message("lobby.update")
    for connection in tuple(deps.hub.subscribers("lobby")):
        connection.send(message)


@router.get("")
async def list_games(deps: Deps, principal: Principal) -> list[LobbyGameSummary]:
    return [LobbyGameSummary.of(s) for s in await deps.games.list_joinable()]


@router.post("", status_code=201)
async def create_game(
    body: CreateGameRequest, deps: Deps, principal: Principal
) -> GameSnapshot:
    try:
        loaded = deps.maps.load_with_digest(MapId(body.map_id))
    except InvalidMapError as exc:
        raise ApiError(ApiErrorCode.MAP_UNKNOWN, 404, "no such map") from exc

    if body.preset_id is None:
        preset = await deps.presets.get_default()
        if preset is None:
            raise ApiError(ApiErrorCode.NO_DEFAULT_PRESET, 409, "no default preset is configured")
    else:
        preset = await deps.presets.get(body.preset_id)
        if preset is None:
            raise ApiError(ApiErrorCode.PRESET_UNKNOWN, 404, "no such preset")

    game_id = GameId(uuid.uuid4().hex)
    host = PlayerId(principal.user_id)
    # §6.2's `tx1`. Not routed through `TransactionContext.append`: the
    # optimistic check is `UPDATE games ... WHERE last_seq = :expected`, and
    # at genesis there is no row for it to match.
    await deps.games.create(
        game_id=game_id,
        map_id=MapId(body.map_id),
        rules=preset.rules,
        host_id=host,
        map_sha256=loaded.sha256,
        preset_id=preset.preset_id,
        operation_id=f"genesis:{game_id}",
    )
    # ...and then the host joins through the runtime, like anyone else. The
    # crash window between the two is a player-less lobby, which §5.6's
    # sweep collects after five minutes and `list_joinable` hides meanwhile.
    runtime = await _submit(deps, game_id, JoinGame(host, await _display_name(deps, principal)))
    await _publish_lobby(deps)
    return _snapshot(deps, runtime, principal)


@router.get("/{game_id}")
async def get_game(game_id: str, deps: Deps, principal: Principal) -> GameSnapshot:
    """§9.3's first paint: the same projection the socket sends, so the page
    renders while the socket is still connecting."""
    runtime = await deps.manager.get(GameId(game_id))
    state = runtime.state
    if state.phase is not Phase.LOBBY and PlayerId(principal.user_id) not in state.players:
        # A live game's snapshot carries the open question. Spectating is
        # Spec 2; a lobby has no question and is readable by anyone signed
        # in, which is how a player decides whether to join it.
        raise ApiError(ApiErrorCode.FORBIDDEN, 403, "not a participant in that game")
    return _snapshot(deps, runtime, principal)


@router.post("/{game_id}/join")
async def join_game(game_id: str, deps: Deps, principal: Principal) -> GameSnapshot:
    runtime = await _submit(
        deps,
        GameId(game_id),
        JoinGame(PlayerId(principal.user_id), await _display_name(deps, principal)),
    )
    await _publish_lobby(deps)
    return _snapshot(deps, runtime, principal)


@router.post("/{game_id}/start")
async def start_game(game_id: str, deps: Deps, principal: Principal) -> GameSnapshot:
    runtime = await _submit(deps, GameId(game_id), StartGame(PlayerId(principal.user_id)))
    await _publish_lobby(deps)
    return _snapshot(deps, runtime, principal)
```

A game that does not exist reaches `manager.get`, whose loader raises — Plan 4's `GameLoader` raises `PermanentReplayFailure` for an empty stream, which `_load` turns into `GameUnrecoverable` and a 503. That is wrong for "no such game": add an explicit existence check at the top of `get_game`, `join_game` and `start_game` — `if await deps.games.get_summary(GameId(game_id)) is None: raise ApiError(NOT_FOUND, 404, "no such game")` — so a typo in a URL is a 404 and never records a `Failed` registry entry for a game id that was never real.

- [ ] **Step 4: Add the request/response schemas and the lobby message**

In `schemas/games.py`:

```python
class CreateGameRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    map_id: str = Field(min_length=1, max_length=64)
    # Optional: absent means the default preset (§7's "never zero" is a
    # migration, so absent is the ordinary case, not the fallback).
    preset_id: str | None = None


class LobbyGameSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    game_id: str
    map_id: str
    host_id: str
    status: str
    player_count: int
    max_players: int

    @classmethod
    def of(cls, summary: GameSummary) -> "LobbyGameSummary":
        return cls(
            game_id=str(summary.game_id),
            map_id=str(summary.map_id),
            host_id=str(summary.host_id),
            status=summary.status,
            player_count=summary.player_count,
            max_players=summary.max_players,
        )
```

and replace `AppDependencies.lobby_message` with the real one:

```python
    async def lobby_message(self, kind: str) -> LobbyMessage:
        return LobbyMessage(
            type=kind,  # type: ignore[arg-type]
            games=tuple(
                LobbyGame(
                    game_id=str(s.game_id), map_id=str(s.map_id), host_id=str(s.host_id),
                    status=s.status, player_count=s.player_count, max_players=s.max_players,
                )
                for s in await self.games.list_joinable()
            ),
        )
```

- [ ] **Step 5: Run the tests**

Run: `cd backend && uv run pytest tests/api -v --no-cov && uv run mypy --strict`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add backend/src/triviador/api backend/tests/api
git commit -m "feat(api): the game endpoints, with creation's two commits and one mutation path"
```

---

## Task 19: `export-contracts` and `admin-create`

**Files:**
- Create: `backend/src/triviador/api/contracts.py`, `backend/src/triviador/cli.py`
- Create: `contracts/.gitignore` (empty — the directory is committed with its contents)
- Test: `backend/tests/api/test_contracts.py`, `backend/tests/db/test_admin_create.py`

**Interfaces:**
- Consumes: `create_app`, every schema module, `ApiErrorCode`, `RejectCode`, `UserRepository`, `Argon2Hasher`, `Settings`.
- Produces: `export_contracts(out_dir) -> None` writing `openapi.json`, `rest.schema.json`, `ws.schema.json`, `errors.json`; `admin_create(...) -> AdminCreateOutcome` (`CREATED`/`ALREADY_EXISTS`/`REFUSED`); `main(argv) -> int`.

- [ ] **Step 1: Write the failing tests**

`backend/tests/api/test_contracts.py`:

```python
"""§7's export. Four documents, each with a job.

`rest.schema.json` is separate from `openapi.json` because
`json-schema-to-zod` cannot consume an OpenAPI document's
`components.schemas` as though the document were JSON Schema — the `$ref`s
point at `#/components/schemas/...`, which is not a JSON Schema location.
"""

import json
from pathlib import Path

import pytest

from triviador.api.contracts import export_contracts
from triviador.api.errors import ApiErrorCode
from triviador.domain.game.actions import RejectCode


@pytest.fixture
def contracts(tmp_path: Path) -> dict[str, dict]:
    export_contracts(tmp_path)
    return {p.name: json.loads(p.read_text()) for p in tmp_path.glob("*.json")}


def test_all_four_documents_are_written(contracts: dict[str, dict]) -> None:
    assert set(contracts) == {"openapi.json", "rest.schema.json", "ws.schema.json", "errors.json"}


def test_the_rest_schema_resolves_its_refs_locally(contracts: dict[str, dict]) -> None:
    """Every `$ref` must point inside `$defs`, or the generator emits a
    module full of `any`."""
    text = json.dumps(contracts["rest.schema.json"])
    assert "#/components/schemas/" not in text
    defs = contracts["rest.schema.json"]["$defs"]
    for ref in _refs(contracts["rest.schema.json"]):
        assert ref.startswith("#/$defs/") and ref.removeprefix("#/$defs/") in defs


def test_the_rest_schema_covers_every_player_facing_response(
    contracts: dict[str, dict],
) -> None:
    defs = contracts["rest.schema.json"]["$defs"]
    for name in ("GameSnapshot", "LobbyGameSummary", "MapDetail", "Me", "ErrorEnvelope"):
        assert name in defs


def test_the_ws_schema_carries_both_directions(contracts: dict[str, dict]) -> None:
    defs = contracts["ws.schema.json"]["$defs"]
    assert "SubmitAnswerFrame" in defs
    assert "UpdateMessage" in defs


def test_every_client_frame_forbids_extra_properties(contracts: dict[str, dict]) -> None:
    """§6.5's strictness has to survive the export, or the generated Zod
    objects are not `.strict()` and the guarantee stops at the backend."""
    defs = contracts["ws.schema.json"]["$defs"]
    for name in ("SubscribeFrame", "PingFrame", "SurrenderFrame", "SubmitAnswerFrame"):
        assert defs[name]["additionalProperties"] is False


def test_no_exported_schema_declares_an_actor(contracts: dict[str, dict]) -> None:
    for name, schema in contracts["ws.schema.json"]["$defs"].items():
        assert "actor_id" not in schema.get("properties", {}), name


def test_no_exported_schema_declares_an_answer_field(contracts: dict[str, dict]) -> None:
    """The structural guarantee, checked at the contract boundary: if the
    field is not in the schema, the generated TypeScript has no name for it
    and no client can read one."""
    forbidden = {"is_correct", "correct_value", "numeric_answer", "correct_choice_index"}
    for document in ("rest.schema.json", "ws.schema.json"):
        for name, schema in contracts[document]["$defs"].items():
            if name.startswith(("QuestionResolved", "RevealedAnswer")):
                continue  # the reveal, which is supposed to carry them
            assert not (set(schema.get("properties", {})) & forbidden), (document, name)


def test_errors_exports_both_enums_and_they_stay_disjoint(contracts: dict[str, dict]) -> None:
    errors = contracts["errors.json"]
    assert set(errors["api_error_code"]) == {c.value for c in ApiErrorCode}
    assert set(errors["reject_code"]) == {c.value for c in RejectCode}
    assert not set(errors["api_error_code"]) & set(errors["reject_code"])


def _refs(node: object) -> list[str]:
    if isinstance(node, dict):
        found = [node["$ref"]] if "$ref" in node else []
        return found + [r for v in node.values() for r in _refs(v)]
    if isinstance(node, list):
        return [r for v in node for r in _refs(v)]
    return []
```

`backend/tests/db/test_admin_create.py` — Spec 1 §10.1's three semantics, verbatim, against real PostgreSQL:

```python
import pytest

from triviador.cli import AdminCreateOutcome, admin_create
from triviador.db.repositories.auth import UserRepository
from triviador.db.security import Argon2Hasher
from triviador.services.identity import UserRole

pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="session")]


async def create(sessions, **kw):
    return await admin_create(
        users=UserRepository(sessions),
        hasher=Argon2Hasher(),
        username=kw.get("username", "root"),
        password=kw.get("password", "correct horse"),
        display_name=kw.get("display_name", "Root"),
        force=bool(kw.get("force", False)),
    )


async def test_with_no_admins_it_creates_one(clean_db, sessions) -> None:
    assert await create(sessions) == AdminCreateOutcome.CREATED
    user = await UserRepository(sessions).get_by_username("root")
    assert user is not None and user.role is UserRole.ADMIN


async def test_re_running_with_the_same_username_is_a_no_op(clean_db, sessions) -> None:
    """"Safe in a deployment script" is the requirement: a provisioning
    step that runs on every boot must not fail on the second boot."""
    await create(sessions)
    assert await create(sessions) == AdminCreateOutcome.ALREADY_EXISTS


async def test_a_second_admin_is_refused_without_force(clean_db, sessions) -> None:
    await create(sessions, username="root")
    assert await create(sessions, username="other") == AdminCreateOutcome.REFUSED
    assert await UserRepository(sessions).get_by_username("other") is None


async def test_force_creates_the_second_admin(clean_db, sessions) -> None:
    await create(sessions, username="root")
    assert await create(sessions, username="other", force=True) == AdminCreateOutcome.CREATED


async def test_the_password_is_never_stored_in_the_clear(clean_db, sessions) -> None:
    await create(sessions, password="correct horse")
    user = await UserRepository(sessions).get_by_username("root")
    assert user is not None and "correct horse" not in user.password_hash
```

- [ ] **Step 2: Run to verify they fail**

Run: `cd backend && uv run pytest tests/api/test_contracts.py tests/db/test_admin_create.py -v --no-cov`
Expected: FAIL — neither module exists.

- [ ] **Step 3: Write the exporter**

`backend/src/triviador/api/contracts.py`:

```python
"""§7's four documents.

`openapi.json` is documentation and a second drift signal.
`rest.schema.json` is what the generator actually consumes, exported
separately with `$defs` resolved for the reason §7 gives: an OpenAPI
document's `$ref`s point at `#/components/schemas/...`, which JSON Schema
tooling cannot resolve.
"""

import json
from pathlib import Path
from typing import Any

from pydantic import TypeAdapter
from pydantic.json_schema import models_json_schema

from triviador.api.errors import ApiErrorCode
from triviador.api.schemas.auth import LoginRequest, Me, RedeemRequest
from triviador.api.schemas.errors import ErrorEnvelope
from triviador.api.schemas.games import CreateGameRequest, GameSnapshot, LobbyGameSummary
from triviador.api.schemas.maps import MapDetail, MapSummary
from triviador.api.schemas.ws import ClientMessage, ServerMessage
from triviador.domain.game.actions import RejectCode

REST_MODELS = (
    RedeemRequest, LoginRequest, Me,
    CreateGameRequest, GameSnapshot, LobbyGameSummary,
    MapSummary, MapDetail,
    ErrorEnvelope,
)

REF_TEMPLATE = "#/$defs/{model}"


def rest_schema() -> dict[str, Any]:
    _, schema = models_json_schema(
        [(model, "serialization") for model in REST_MODELS],
        ref_template=REF_TEMPLATE,
        title="TriviadorRest",
    )
    return schema


def ws_schema() -> dict[str, Any]:
    return {
        "title": "TriviadorWs",
        "$defs": {
            **TypeAdapter(ClientMessage).json_schema(ref_template=REF_TEMPLATE).get("$defs", {}),
            **TypeAdapter(ServerMessage).json_schema(ref_template=REF_TEMPLATE).get("$defs", {}),
        },
    }


def errors_schema() -> dict[str, Any]:
    return {
        "api_error_code": sorted(c.value for c in ApiErrorCode),
        "reject_code": sorted(c.value for c in RejectCode),
    }


def export_contracts(out_dir: Path) -> None:
    from triviador.api.app import create_app
    from triviador.api.deps import AppDependencies

    out_dir.mkdir(parents=True, exist_ok=True)
    # `app.openapi()` needs an app but not a database: `create_app` takes
    # its dependencies as an argument precisely so this is possible.
    app = create_app(AppDependencies.placeholder())
    documents = {
        "openapi.json": app.openapi(),
        "rest.schema.json": rest_schema(),
        "ws.schema.json": ws_schema(),
        "errors.json": errors_schema(),
    }
    for name, document in documents.items():
        # `sort_keys` and a trailing newline: the drift check is
        # `git diff --exit-code`, so byte-for-byte stability across Python
        # versions and dict orderings is the whole point.
        (out_dir / name).write_text(
            json.dumps(document, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
```

`AppDependencies.placeholder()` is a classmethod returning an instance whose every port is a trivial object that raises on use — enough to build the router table and nothing more. Write it in `deps.py` with a docstring saying exactly that, and keep it out of `build_dependencies`'s path.

- [ ] **Step 4: Write the CLI**

`backend/src/triviador/cli.py`:

```python
"""`uv run triviador <command>`.

Two commands. `export-contracts` needs no database at all;
`admin-create` needs one, and is the bootstrap Spec 1 §10.1 specifies —
with its three outcomes spelled out so it is safe in a deployment script.
"""

import argparse
import asyncio
import sys
import uuid
from enum import StrEnum
from pathlib import Path

from triviador.api.contracts import export_contracts
from triviador.config import get_settings
from triviador.db.engine import engine_for, sessionmaker_for
from triviador.db.repositories.auth import UserRepository
from triviador.db.security import Argon2Hasher
from triviador.domain.ids import UserId
from triviador.services.identity import PasswordHasher, UserRole, UserStore


class AdminCreateOutcome(StrEnum):
    CREATED = "created"
    ALREADY_EXISTS = "already_exists"
    REFUSED = "refused"


async def admin_create(
    *,
    users: UserStore,
    hasher: PasswordHasher,
    username: str,
    password: str,
    display_name: str,
    force: bool,
) -> AdminCreateOutcome:
    """Spec 1 §10.1, exactly:

        no admins exist                       → create
        same username already exists as admin → success, no-op
        another admin already exists          → refuse unless --force

    The middle case is what makes this safe to run on every boot; the last
    is what stops a provisioning script from quietly minting admins.
    """
    existing = await users.get_by_username(username)
    if existing is not None:
        return (
            AdminCreateOutcome.ALREADY_EXISTS
            if existing.role is UserRole.ADMIN
            else AdminCreateOutcome.REFUSED
        )
    if await users.count_admins() > 0 and not force:
        return AdminCreateOutcome.REFUSED

    await users.create(
        user_id=UserId(uuid.uuid4().hex),
        username=username,
        password_hash=hasher.hash(password),
        display_name=display_name,
        role=UserRole.ADMIN,
    )
    return AdminCreateOutcome.CREATED


async def _admin_create_command(args: argparse.Namespace) -> int:
    settings = get_settings()
    async with engine_for(settings.database_url) as engine:
        outcome = await admin_create(
            users=UserRepository(sessionmaker_for(engine)),
            hasher=Argon2Hasher(),
            username=args.username,
            password=args.password,
            display_name=args.display_name or args.username,
            force=args.force,
        )
    print(outcome.value)
    return 1 if outcome is AdminCreateOutcome.REFUSED else 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="triviador")
    commands = parser.add_subparsers(dest="command", required=True)

    export = commands.add_parser("export-contracts")
    export.add_argument("--out", type=Path, required=True)

    admin = commands.add_parser("admin-create")
    admin.add_argument("--username", required=True)
    admin.add_argument("--password", required=True)
    admin.add_argument("--display-name")
    admin.add_argument("--force", action="store_true")

    args = parser.parse_args(argv)
    if args.command == "export-contracts":
        export_contracts(args.out)
        return 0
    return asyncio.run(_admin_create_command(args))


if __name__ == "__main__":
    sys.exit(main())
```

`--password` on the command line lands in the shell history and in `ps`. That is a real weakness and it is Spec 1 §10.1's stated interface, so it stays — but the `admin-create` help text says so, and Plan 8's deployment notes should prefer a leading space or a here-string.

- [ ] **Step 5: Export the contracts and commit them**

Run:
```bash
cd backend && uv run triviador export-contracts --out ../contracts
cd .. && git add contracts && git status --short contracts
```
Expected: four new JSON files.

- [ ] **Step 6: Run the tests**

Run: `cd backend && uv run pytest tests/api/test_contracts.py tests/db/test_admin_create.py -v && uv run mypy --strict`
Expected: PASS

- [ ] **Step 7: Commit**

```bash
git add backend/src/triviador backend/tests contracts
git commit -m "feat(api): export the four contract documents, and the admin bootstrap command"
```

---

## Task 20: `pnpm codegen` — Zod from the contracts, with a drift gate

**Files:**
- Create: `frontend/package.json`, `frontend/pnpm-lock.yaml`, `frontend/.gitignore`, `frontend/scripts/codegen.mjs`
- Create: `frontend/shared/api/generated/{public,ws,errors}.ts` (generated, committed)
- Test: `backend/tests/api/test_contracts.py` (one added test), plus the drift command itself

**Interfaces:**
- Consumes: `contracts/{rest.schema.json,ws.schema.json,errors.json}`.
- Produces: `pnpm codegen`; `shared/api/generated/public.ts` (one exported Zod schema and inferred type per REST `$def`), `ws.ts` (same for the socket envelope), `errors.ts` (the two enums as `z.enum`).

- [ ] **Step 1: Write the scaffold**

`frontend/package.json` — the contracts consumer only. Vite, React, Tailwind and the app itself are Plan 6; adding them now would mean choosing versions for an application nobody has started.

```json
{
  "name": "triviador-frontend",
  "private": true,
  "type": "module",
  "scripts": {
    "codegen": "node scripts/codegen.mjs",
    "codegen:check": "node scripts/codegen.mjs && git diff --exit-code -- shared/api/generated"
  },
  "devDependencies": {
    "json-schema-to-zod": "^2.6.0",
    "zod": "^3.24.0"
  },
  "packageManager": "pnpm@9.15.0"
}
```

`frontend/.gitignore`:

```
node_modules/
```

Then `cd frontend && pnpm install` to produce `pnpm-lock.yaml`, which is committed.

- [ ] **Step 2: Write the generator**

`frontend/scripts/codegen.mjs`:

```js
// Generates one Zod schema per definition in the exported contracts.
//
// Split into three modules on purpose (§7). A single `rest.ts` would pull
// every top-level Zod construction into the player bundle regardless of
// tree-shaking, because schema construction is a side-effecting top-level
// expression. `admin.ts` is absent because there are no admin DTOs yet —
// Plan 7 adds `contracts/admin.schema.json` and this script picks it up
// with no change.

import { mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { jsonSchemaToZod } from "json-schema-to-zod";

const here = dirname(fileURLToPath(import.meta.url));
const contracts = resolve(here, "../../contracts");
const out = resolve(here, "../shared/api/generated");

const HEADER = `// GENERATED by \`pnpm codegen\` from ../../../../contracts. Do not edit.
// CI runs \`pnpm codegen:check\`, which fails on any drift.
import { z } from "zod";
`;

/** Emit every `$def` of one document as its own exported schema + type. */
function emitDocument(file, outFile) {
  const document = JSON.parse(readFileSync(resolve(contracts, file), "utf8"));
  const defs = document.$defs ?? {};
  const names = Object.keys(defs).sort();
  const body = names
    .map((name) => {
      // `withoutDefaults: false` keeps `.default(...)`; `module: "none"`
      // returns the bare expression so we control the export shape.
      const schema = jsonSchemaToZod(
        { ...defs[name], $defs: defs },
        { module: "none", name: undefined },
      );
      return `export const ${camel(name)}Schema = ${schema};\nexport type ${name} = z.infer<typeof ${camel(name)}Schema>;`;
    })
    .join("\n\n");
  writeFileSync(resolve(out, outFile), `${HEADER}\n${body}\n`, "utf8");
  console.log(`${outFile}: ${names.length} schemas`);
}

function camel(name) {
  return name.charAt(0).toLowerCase() + name.slice(1);
}

function emitErrors() {
  const { api_error_code, reject_code } = JSON.parse(
    readFileSync(resolve(contracts, "errors.json"), "utf8"),
  );
  const enumOf = (values) => `z.enum([${values.map((v) => JSON.stringify(v)).join(", ")}])`;
  writeFileSync(
    resolve(out, "errors.ts"),
    `${HEADER}
export const apiErrorCodeSchema = ${enumOf(api_error_code)};
export type ApiErrorCode = z.infer<typeof apiErrorCodeSchema>;

export const rejectCodeSchema = ${enumOf(reject_code)};
export type RejectCode = z.infer<typeof rejectCodeSchema>;

// One closed union, as the envelope declares it. The two value sets are
// disjoint — the backend asserts that — so this discriminates cleanly.
export const errorCodeSchema = z.union([apiErrorCodeSchema, rejectCodeSchema]);
export type ErrorCode = z.infer<typeof errorCodeSchema>;
`,
    "utf8",
  );
  console.log(`errors.ts: ${api_error_code.length + reject_code.length} codes`);
}

mkdirSync(out, { recursive: true });
emitDocument("rest.schema.json", "public.ts");
emitDocument("ws.schema.json", "ws.ts");
emitErrors();
```

- [ ] **Step 3: Generate, inspect, and commit the output**

Run:
```bash
cd frontend && pnpm codegen
grep -c "export const" shared/api/generated/public.ts shared/api/generated/ws.ts
grep -n "strict()" shared/api/generated/ws.ts | head
```
Expected: a schema per definition in each file, and `.strict()` on every client-frame object — `json-schema-to-zod` emits it from `"additionalProperties": false`, which `test_contracts.py` already asserts is present in the export. **If `.strict()` is absent**, the strictness guarantee stops at the backend: fix it by post-processing the emitted schema in this script rather than by relaxing the test.

- [ ] **Step 4: Prove the drift gate fails on drift**

Run:
```bash
cd frontend && printf '\n// tampered\n' >> shared/api/generated/errors.ts
pnpm codegen:check ; echo "exit=$?"
git checkout -- shared/api/generated
```
Expected: non-zero exit. A gate nobody has watched fail is a gate nobody can trust — the same reason `test_layering.py` writes probe files.

- [ ] **Step 5: Add the round-trip test on the backend side**

Append to `backend/tests/api/test_contracts.py`:

```python
def test_the_committed_contracts_match_a_fresh_export(tmp_path: Path) -> None:
    """The other half of the drift gate, on the side that can run without
    node: the files under `contracts/` are what this backend produces
    right now. CI runs both; a developer who changes a schema and forgets
    to export sees it here first."""
    root = Path(__file__).resolve().parents[3] / "contracts"
    export_contracts(tmp_path)
    for produced in sorted(tmp_path.glob("*.json")):
        assert produced.read_text() == (root / produced.name).read_text(), produced.name
```

- [ ] **Step 6: Run everything**

Run: `cd backend && uv run pytest -q && uv run ruff check && uv run mypy --strict`
Expected: PASS

- [ ] **Step 7: Commit**

```bash
git add frontend contracts backend/tests/api/test_contracts.py
git commit -m "feat(contracts): pnpm codegen from the exported schemas, with a drift gate"
```

---

## Task 21: A whole game, over HTTP and a real socket, against real PostgreSQL

The payoff. Everything before this proved a piece; this proves the seams.

**Files:**
- Create: `backend/tests/api/integration/__init__.py`, `backend/tests/api/integration/conftest.py`, `backend/tests/api/integration/test_play_through_http.py`

**Interfaces:**
- Consumes: `build_app`, `Settings`, `alembic upgrade head`, `tests/db/conftest`'s seeding helpers, `tests/runtime/integration/conftest.write_grid_map`.
- Produces: nothing importable — this is the end of the chain.

- [ ] **Step 1: Write the conftest**

`backend/tests/api/integration/conftest.py`:

```python
"""The real app, over real PostgreSQL, driven by Starlette's `TestClient`.

**Why the tests here are synchronous.** `TestClient` runs the ASGI app on
its own event loop in its own thread — that is what lets it offer a
*blocking* `websocket_connect`, which is the only ergonomic way to script
a socket conversation. An `async def` test would then be nesting two loops,
and `tests/db/conftest.py`'s session-scoped asyncpg engine is bound to the
outer one. So this directory does not use those fixtures at all: it owns a
throwaway engine per helper call, via `asyncio.run`, on whichever thread
the caller happens to be. Every connection is opened and closed inside one
`asyncio.run`, so nothing is ever shared across loops.

**Why real time passes here.** §12.2 forbids waiting on wall-clock time for
*game logic* — and this suite does not: every window is closed by a player
answering, never by a timeout. The one unavoidable wait is `MediaWarmup`,
which is a fixed duration by construction (ADR-003 forbids a rule that
depends on client readiness), so the preset sets it to the 1 s floor. Spec
1 §12.4's Playwright smoke has exactly the same property.
"""

import asyncio
import os
from collections.abc import Iterator
from dataclasses import replace
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import text

from tests.runtime.integration.conftest import write_grid_map
from triviador.config import Settings
from triviador.domain.game.rules import GameRules

pytestmark = pytest.mark.integration

DATABASE_URL = os.environ.get(
    "TRIVIADOR_TEST_DATABASE_URL",
    "postgresql+asyncpg://triviador:triviador@127.0.0.1:5433/triviador_test",
)

# 2 players, one expansion round, one battle round, every window at its
# floor. `required_question_budget` for this is 4 numeric + 2 MC.
FAST_RULES = GameRules(
    player_count=2,
    expansion_rounds=1,
    battle_rounds=1,
    base_hp=1,
    answer_timeout_ms=3_000,
    pick_timeout_ms=3_000,
    warmup_ms=1_000,
    claims_by_rank=(2, 1),
    pts_base=1000,
    pts_territory=200,
    pts_conquered=400,
    pts_defense=100,
)


def run(coro):  # type: ignore[no-untyped-def]
    """Every database helper here opens its own engine inside its own
    `asyncio.run`, so no connection outlives the loop it was created on."""
    return asyncio.run(coro)


@pytest.fixture(scope="session")
def migrated() -> None:
    config = Config(str(Path(__file__).resolve().parents[3] / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", DATABASE_URL)
    command.upgrade(config, "head")


@pytest.fixture
def seeded(migrated: None, tmp_path: Path) -> Path:
    """Truncate, then seed: two users, an invite for each, a question bank
    covering `FAST_RULES`, and a `fast` preset. Returns the maps root."""
    ...  # see Step 2


@pytest.fixture
def client(seeded: Path) -> Iterator[TestClient]:
    from triviador.api.app import build_app

    settings = Settings(  # type: ignore[call-arg]
        database_url=DATABASE_URL,
        allowed_origins=("http://testserver",),
        allowed_hosts=("testserver",),
        cookie_secure=False,
        maps_root=seeded,
        log_format="console",
    )
    with TestClient(build_app(settings), base_url="http://testserver") as client:
        client.headers["Origin"] = "http://testserver"
        yield client
```

- [ ] **Step 2: Fill in `seeded`**

Reuse the seeding helpers rather than re-authoring them: `tests/db/conftest.py` already has `_seed_user`, `_seed_category`, `_seed_numeric_question` and `_seed_mc_question`, each taking an `async_sessionmaker`. Wrap them:

```python
@pytest.fixture
def seeded(migrated: None, tmp_path: Path) -> Path:
    from dataclasses import asdict

    from sqlalchemy import insert
    from tests.db.conftest import (
        _seed_category, _seed_mc_question, _seed_numeric_question, _seed_user,
    )
    from triviador.db.engine import engine_for, sessionmaker_for
    from triviador.db.models.presets import RulePreset
    from triviador.db.security import Argon2Hasher
    from triviador.db.seed import DEFAULT_PRESET_RULES

    async def seed() -> None:
        async with engine_for(DATABASE_URL) as engine:
            sessions = sessionmaker_for(engine)
            async with sessions() as db, db.begin():
                # Order matters: `game_events` and `game_players` reference
                # `games`, which references `users` and `rule_presets`.
                for table in ("game_events", "game_players", "games", "sessions",
                              "invite_codes", "question_choices", "question_numeric",
                              "questions", "categories", "users", "rule_presets"):
                    await db.execute(text(f"TRUNCATE {table} CASCADE"))
                # Re-seed migration 0002's row from the same frozen literal
                # it used. Truncating and restoring beats excluding the
                # table: an excluded table preserves *mutations* between
                # tests, and this suite creates games that read the default.
                await db.execute(insert(RulePreset).values(
                    id="default", name="Default", is_default=True,
                    rules=dict(DEFAULT_PRESET_RULES), version=1, is_active=True,
                ))
            hasher = Argon2Hasher()
            for name in ("alice", "bob"):
                await _seed_user(sessions, name)
                async with sessions() as db, db.begin():
                    await db.execute(text(
                        "UPDATE users SET username = :u, password_hash = :p, "
                        "display_name = :d, role = 'player' WHERE id = :u"
                    ), {"u": name, "p": hasher.hash("correct horse"), "d": name.title()})
            await _seed_category(sessions)
            for i in range(4):
                await _seed_numeric_question(sessions, f"num-{i}")
            for i in range(2):
                await _seed_mc_question(sessions, f"mc-{i}")
            async with sessions() as db, db.begin():
                await db.execute(insert(RulePreset).values(
                    id="fast", name="Fast", is_default=False,
                    rules=asdict(FAST_RULES), version=1, is_active=True,
                ))

    run(seed())
    write_grid_map(tmp_path / "grid")
    return tmp_path
```

`_seed_user` creates a row with the id as username; the `UPDATE` above gives it the username, password and display name this suite signs in with. If `_seed_user`'s signature does not permit that, extend it in `tests/db/conftest.py` with keyword arguments defaulting to today's behaviour rather than duplicating it here.

The `TRUNCATE` list includes `rule_presets` and the fixture puts the default back — the same baseline-per-test discipline `tests/db/conftest.py`'s `default_preset` fixture uses, and for the same reason: excluding the table would let one test's mutation decide whether the next one passes.

- [ ] **Step 3: Write the play-through**

`backend/tests/api/integration/test_play_through_http.py`:

```python
"""Create → join → start → FINISHED, over HTTP and one real socket each.

Not a suite: one scenario proving the seams line up. Everything it asserts
has been asserted in isolation somewhere above; what is new here is that
the composition root, PostgreSQL, the runtime, the hub and the projection
are all the real ones.
"""

import json

import pytest
from fastapi.testclient import TestClient

pytestmark = pytest.mark.integration


def sign_in(client: TestClient, username: str) -> dict:
    response = client.post(
        "/api/auth/login", json={"username": username, "password": "correct horse"}
    )
    assert response.status_code == 200, response.text
    return response.json()


def until(socket, *types: str, limit: int = 40) -> list[dict]:
    """Read frames until one of `types` arrives, returning everything read.

    Bounded and raising rather than looping: a wedged server must fail the
    test that provoked it, not hang CI with no indication of where.
    """
    seen: list[dict] = []
    for _ in range(limit):
        message = json.loads(socket.receive_text())
        seen.append(message)
        if message["type"] in types:
            return seen
    raise AssertionError(f"never saw {types}; saw {[m['type'] for m in seen]}")


def answer(socket, game_id: str, turn: dict, command_id: str) -> None:
    """Answer whatever kind of question is open.

    Switching on `turn["question"]["kind"]` is not defensive coding — it is
    required. `FAST_RULES` needs two multiple-choice questions
    (`required_question_budget` gives `multiple_choice = battle_rounds *
    player_count`), every battle duel presents one, and a numeric payload
    sent to an MC window is rejected with `ANSWER_KIND_MISMATCH`. The
    window would then close on its own 3 s deadline instead of on a
    player's answer — both slower and a different scenario from the one
    this test claims to run.
    """
    if turn["question"]["kind"] == "multiple_choice":
        payload: dict[str, object] = {"kind": "choice", "idx": 0}
    else:
        payload = {"kind": "numeric", "value": "1"}
    socket.send_json({
        "type": "submit_answer", "command_id": command_id, "game_id": game_id,
        "deadline_id": turn["deadline_id"], "payload": payload,
    })


def test_two_players_play_a_whole_game(client: TestClient) -> None:
    alice = sign_in(client, "alice")
    alice_cookies = dict(client.cookies)

    created = client.post("/api/games", json={"map_id": "grid", "preset_id": "fast"})
    assert created.status_code == 201, created.text
    game_id = created.json()["state"]["game_id"]
    assert [p["player_id"] for p in created.json()["state"]["players"]] == [alice["user_id"]]

    # Bob signs in on the same client, which replaces the cookie.
    sign_in(client, "bob")
    bob_cookies = dict(client.cookies)
    joined = client.post(f"/api/games/{game_id}/join")
    assert joined.status_code == 200, joined.text
    assert len(joined.json()["state"]["players"]) == 2

    with client.websocket_connect("/ws", cookies=bob_cookies) as bob_ws:
        client.cookies.update(alice_cookies)
        with client.websocket_connect("/ws", cookies=alice_cookies) as alice_ws:
            for socket in (alice_ws, bob_ws):
                assert json.loads(socket.receive_text())["type"] == "hello"
                socket.send_json({"type": "subscribe", "topic": f"game:{game_id}"})
                until(socket, "game.snapshot")

            started = client.post(f"/api/games/{game_id}/start")
            assert started.status_code == 200, started.text

            # §9.6: the warmup window opens before any question, and the
            # snapshot already carries every image to prefetch.
            warmup = until(alice_ws, "game.update")[-1]
            assert warmup["state"]["turn"]["kind"] == "media_warmup"

            _play_to_finish(client, game_id, alice_ws, bob_ws)

    final = client.get(f"/api/games/{game_id}")
    assert final.json()["state"]["phase"] == "finished"
    assert final.json()["state"]["winner_id"] is not None
```

`_play_to_finish` drives the game generically rather than scripting a fixed sequence, because base placement and pick order are randomised. Loop reading `game.update`s and act on `state.turn`, dispatching on `turn["kind"]`:

| turn kind | what happens |
|---|---|
| `media_warmup` | nothing — a fixed window, and the only real wait in the run |
| `expansion_question` · `battle_duel` · `neutral_challenge` · `final_tiebreak` | `answer(socket, game_id, turn, ...)` from **every** socket not already in `turn["answered"]` |
| `expansion_picking` | the socket whose `your_options.pick` is non-empty sends `pick_region` with `pick[0]` |
| `battle_target_select` | the socket whose `your_options.attack` is non-empty sends `select_attack_target` with `attack[0]` |

Continue until `state["phase"] == "finished"`, recording each `turn["question"]["kind"]` into a `kinds_seen` set as you go. Three details decide whether this terminates:

- **Read from both sockets.** Each receives its own projection and `your_options` is populated on exactly one of them per turn. A loop reading only Alice's stream never sees the turn where Bob is the picker, and waits out a 3 s deadline instead of acting.
- **Both players answer every question window.** A window resolves when everyone has answered or when it expires, and the whole claim of this scenario is that none expires. `turn["answered"]` is what to check against.
- **Both question kinds must appear.** Expansion questions are numeric and battle duels are multiple-choice; a run that only ever sent numeric payloads would silently be answering nothing during the battle round.

Cap the loop and raise on exhaustion, like `until`.

Assert along the way, once each:

```python
# Both question kinds were exercised — otherwise the MC path never ran and
# this passed as a numeric-only game that timed out its battle round.
assert kinds_seen == {"numeric", "multiple_choice"}

# §8.4's batch sequencing holds for every update the client applies.
assert update["base_seq"] == last_seq and update["seq"] > update["base_seq"]
last_seq = update["seq"]

# §8.7: before resolution, neither socket has been told the answer.
assert "correct_value" not in json.dumps(update["state"]["turn"])

# ...and after it, both have.
resolved = next(e for e in update["events"] if e["type"] == "question_resolved")
assert resolved["correct_value"] is not None
```

- [ ] **Step 4: Add the guard this directory needs**

Copy `tests/runtime/integration/conftest.py`'s `pytest_collection_modifyitems` pattern, adapted: every module here must carry `pytestmark = pytest.mark.integration`. The loop-scope half does **not** apply — these tests are synchronous by design (Step 1) — so assert only the marker, and say why in the message.

- [ ] **Step 5: Run it**

Run: `cd backend && docker compose -f docker-compose.test.yml up -d && uv run pytest tests/api/integration -v -m integration`
Expected: PASS in a few seconds — one 1 s warmup plus round trips.

- [ ] **Step 6: Run the whole suite, both lanes**

Run:
```bash
cd backend && uv run pytest -q -m "not integration"
uv run pytest -q
uv run ruff check && uv run ruff format --check && uv run mypy --strict
cd ../frontend && pnpm codegen:check
```
Expected: all green.

- [ ] **Step 7: Commit**

```bash
git add backend/tests/api/integration
git commit -m "test(api): a whole game over HTTP and two sockets, against real PostgreSQL"
```

---

## Coverage: what this plan claims, and what it does not

Checked against the spec after the tasks were written, section by section.

| Spec | Where |
|---|---|
| §6.1 auth · maps · games · health | Tasks 7, 17, 18 |
| §6.1 admin routes | **Plan 7** |
| §6.2 game creation's two commits | Task 18 |
| §6.3 error envelope, every row of the table | Task 3 |
| §6.4 origin checking, CORS disabled | Task 8 (REST), Task 16 (`4403` on the handshake) |
| §6.5 hub, principal, strict actorless frames | Tasks 13–16 |
| §7 export-contracts, codegen, drift | Tasks 19, 20 |
| §10.4 config, both startup assertions | Task 1 |
| §10.5 startup order | Task 17's lifespan |
| §10.6 health, degraded games | Task 17 |
| §10.10 logging, request id, redaction | Task 4 |
| §11 Layer 3 list, every line | Tasks 3, 4, 8, 13, 16 |
| Spec 1 §8.1–8.8 realtime protocol | Tasks 9–16 |
| Spec 1 §9.3 first paint | Task 18's `GET /api/games/{id}` |
| Spec 1 §9.6 media prefetch | Task 11 |
| Spec 1 §10.1 admin bootstrap | Task 19 |
| Spec 1 §11.1 four classes, four close codes | Tasks 3, 14, 15, 16 |
| Spec 1 §12.3 drift · redaction · topic authz · revocation | Tasks 20, 9/11/12/19, 16, 14 |

Five things the spec mentions in a section this plan touches, which belong elsewhere and are not built here:

1. **`apiFetch`'s transport error** (§6.3's last paragraph) — a proxy `502`, an HTML error page, a truncated body. That is frontend code and it is Plan 6's; this plan's obligation is the half that makes it possible, which is that a body from *this* server is always an envelope.
2. **Serving media bytes.** The snapshot emits `{MEDIA_PUBLIC_BASE}/{asset_id}` URLs (Task 11); the route behind them is Garage's, in Plans 7/8.
3. **Serving `map.svg`.** `MapDetail.svg_url` names it; Caddy serves it (§10.2, Plan 8).
4. **Readiness' Garage assertion** (§10.6). `Readiness` has three flags because there are three things to check today; Plan 8 adds the fourth alongside the Garage init it verifies.
5. **Per-command runtime logging** (§10.10's "every command logs `game_id`, `operation_id`, command type, committed `seq` range, `duration_ms`"). That line is about the consumer loop, not the HTTP layer — it belongs where the command executes. This plan builds the request-side half (a request id on every line) and the redaction processor that both halves share.

And one test §12.3 names that cannot be completed here: **"deactivating a user closes their open socket with `4401`"**. The mechanism is built and tested in Task 14 (`Hub.close_sessions`), but the endpoint that calls it is Plan 7's `POST /api/admin/users/{id}/deactivate`. Plan 7 owns the end-to-end assertion; what this plan guarantees is that the hub can do it and that a revoked session is refused on the very next request and handshake (Tasks 7, 16).
