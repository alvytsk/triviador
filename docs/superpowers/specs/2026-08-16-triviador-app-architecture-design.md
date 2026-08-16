# Triviador Online — Spec 1B: Application Architecture and Infrastructure

**Date:** 2026-08-16
**Status:** Approved design, ready for implementation planning
**Relationship:** Extends and, in eight named places, **amends**
`docs/superpowers/specs/2026-08-07-triviador-spec1-design.md` (referred to below as
"Spec 1"). Spec 1 remains authoritative for the ruleset, the ADRs, and the product
scope. This document specifies everything between the pure domain core and a
running deployment.

**Scope boundary:** Spec 2 (match history, replay, analytics, spectating, admin live
control, image answers, multiple maps, horizontal scaling) is not pulled forward.

---

## 1. What exists and what this covers

Plan 1 delivered the pure domain core: `domain/{ids,maps,questions,game}` and
`maps/registry.py`, with the full ruleset under test. Everything else in Spec 1 §4's
layout is unbuilt.

This spec covers, in dependency order:

```
§3   domain amendments      corrections the persistence and API layers require
§4   persistence            models, read model, event codec, migrations
§5   services / runtime     GameRuntime, GameManager, ports, failure policy
§6   API                    REST surface, WebSocket hub, authentication principal
§7   contracts              export + codegen pipeline
§8   frontend               map rendering, dispatcher, timer, routing
§9   admin                  lazy route tree, screens
§10  infrastructure         compose, Caddy, Garage, config, migrations, CI, backups
§11  testing additions
§12  plan sequence
§13  open items
```

### 1.1 Deployment profile

Every infrastructure decision below follows from this profile, and several would be
wrong under a different one.

```
host            Windows workstation running Docker Desktop on the WSL2 backend
                — production and development are the same machine
network         LAN only — no public origin, no TLS, no ACME
players         2–4 concurrent, one game at a time in practice
operator        one person
dev             everything in docker-compose
media           Garage (S3-compatible) in compose
ops depth       structured logs, healthchecks, scheduled backups, restore drill
                no metrics stack, no error tracker
```

The host platform is **not** generic Linux Docker, and §10.12 exists because of it:
Windows reboots, user sign-in, Docker Desktop startup, sleep settings, firewall
rules, and scheduled backups all become part of the production infrastructure.

LAN-only means `COOKIE_SECURE=false`, because a `Secure` cookie is never sent over
plain HTTP and authentication would simply fail. Moving to a public origin later is a
configuration change — `ALLOWED_ORIGINS` and `COOKIE_SECURE` — not a rework.

---

## 2. Amendments to Spec 1

These correct Spec 1. Where they conflict, this document wins.

**A-1 — `operation_id` is always server-generated (amends §11.3).**
Spec 1 makes the client's `command_id` the `operation_id` for WS commands. That is
untrusted input: a reused value makes ambiguous-commit reconciliation conclude that
*some other* batch already committed. It also contradicts §8.3, which defines
`command_id` as transport correlation only. `operation_id` is now a server-generated
UUID for every command without exception — WS, REST, timer, watchdog.

**A-2 — the client never supplies `actor_id` (amends §8.3).**
With `actor_id` in the payload, guard 3 only checks that the named actor *is* an
active participant — so one player can submit as another player in the same game and
pass every guard. Client frames carry no actor field; the server derives it from the
authenticated session.

**A-3 — a `seq` gap does not force resync (amends §8.4).**
§8.4's client logic ends in "otherwise → resync", which contradicts §9.1: every
update carries full state, so a gap costs narration, not correctness. The rule is
now three cases, stated in §8.4 of this document.

**A-4 — invite codes get a surrogate key (amends §7).**
`invite_codes(code PK, …)` becomes
`invite_codes(id PK, code_hash UNIQUE, …)`. A secret must not double as its own
administrative identifier, and once the plaintext is returned only at issue there is
no reason to store it retrievably. Same reasoning as §7's opaque session tokens.

**A-5 — a media warmup window precedes the first question (amends §9.6).**
§9.6's fairness argument is vacuous as specified: `QuestionPoolDrawn` and the first
`QuestionPresented` commit in one batch, so the prefetch list and a live deadline
reach the client on the same frame. §3.4 adds a persisted `MediaWarmup` turn.

**A-6 — `GameCreated` is a genesis event (fills a gap in §5.4).**
The implemented reducer cannot `evolve` `GameCreated`, and the comment justifying
that points at `lobby_state()` — which exists only as a **test fixture**
(`tests/conftest.py:123`). There is no production genesis constructor, so recovery
cannot be "fold the log". §3.2 defines `create_initial_state`.

**A-7 — `game_events` carries `schema_version` (extends §7).**
A prohibition on renaming fields is safe but too restrictive for a log that must
outlive every refactor. §4.3 adds versioning and upcasters.

**A-8 — the §6.3 transition matrix grows to 88 cells (extends §6.3).**
The `MediaWarmup` turn adds a row.

---

## 3. Domain amendments

All of §3 is pure-domain work with no I/O, and lands before any persistence exists.

### 3.1 Seat allocation

`reducer.py:160` allocates `seat=len(state.players)`. `reducer.py:897-901` already
documents the consequence: a lobby `PlayerLeft` does not renumber seats, so a later
`JoinGame` re-mints a seat number a remaining player still holds. Harmless in a pure
domain; fatal against `UNIQUE(game_id, seat)`.

```python
used = {p.seat for p in state.players.values()}
seat = min(i for i in range(state.rules.player_count) if i not in used)
```

`JoinGame` already rejects a full lobby, so the range is never exhausted. Regression
test: join → leave-from-middle → join yields distinct seats.

### 3.2 Genesis

```python
def create_initial_state(
    event: GameCreated, game_id: GameId, map_defn: MapDefinition
) -> GameState: ...
```

`GameCreated` is **consumed, never folded**. Recovery is
`create_initial_state(events[0], …)` followed by `fold(evolve, events[1:])`.
`evolve` raises an explicit `GenesisEventNotFoldable` on `GameCreated` rather than
falling through to `NotImplementedError`, so the invariant is stated instead of
inferred. `reducer.py`'s comment is corrected to reference `create_initial_state`
rather than the `lobby_state()` test fixture, which stays where it is as a test
builder.

**Sequencing:** `GameCreated` is **seq 1**. Game creation writes the `games` row and
the genesis event in one transaction before any runtime exists, so `last_seq = 0`
exists only as a pre-insert value and never as a persisted row.
`create_initial_state` returns `state.seq = 1`, and the first runtime append guards
on `expected_last_seq = 1`.

**Map integrity:** `GameCreated` gains `map_sha256` — the digest of the *canonical*
`map.json` serialization:

```python
hashlib.sha256(
    json.dumps(raw, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    .encode("utf-8")
).hexdigest()
```

Canonical form, not file bytes, so reformatting is not a false positive. Recovery
loads via `MapRegistry.load(map_id)`, recomputes, and **refuses to load the game on
mismatch**. This is not exotic: maps are a two-file drop with no version and no
migration, so silent edits are the expected failure mode. `map.svg` is excluded — the
backend reads it for validation only and never to construct domain state.

### 3.3 System-authorized abort

Guard 3 rejects a command whose actor is not an active participant, so the reaper
cannot abort an empty lobby with the existing actor-issued `AbortGame`. A
system-authorized abort variant carries no actor and is legal in any non-terminal
phase.

### 3.4 `MediaWarmup`

```
StartGame       → GameStarted · BasesAssigned · QuestionPoolDrawn
                · MediaWarmupStarted(deadline)        phase EXPANSION, turn MediaWarmup
                  published snapshot carries media_prefetch: string[]
ExpireDeadline  → ExpansionRoundStarted · QuestionPresented
```

New `DeadlineKind.WARMUP`, new `MediaWarmup` turn variant, new
`GameRules.warmup_ms` (default 5 000; bounds 1 000–60 000; checked by
`validate_rules`). `required_question_budget` is unaffected.

The duration is **fixed, never ready-acknowledgements**: waiting for clients would
make a rule depend on presence, which ADR-003 forbids.

Matrix row (A-8):

| Turn \ Command | JOIN | START | ANSWER | PICK | TARGET | EXPIRE | SURRENDER | ABORT |
|---|---|---|---|---|---|---|---|---|
| `EXPANSION/MediaWarmup` | ✗ | ✗ | ✗ | ✗ | ✗ | → | → | → |

Surrender during warmup follows the standard elimination path; if it leaves one
active player, the game finishes without the first question ever being presented.
The §12.1 table test moves from 80 to 88 cells and stays red until extended.

---

## 4. Persistence

### 4.1 Schema changes over Spec 1 §7

```
game_events(game_id, seq, operation_id, type TEXT, schema_version SMALLINT NOT NULL,
            payload JSONB, created_at, PK(game_id, seq))
            INDEX(game_id, operation_id)

invite_codes(id PK, code_hash UNIQUE, created_by, expires_at,
             used_by, used_at, revoked_at)                          -- A-4

rule_presets(… , is_active BOOLEAN NOT NULL DEFAULT true)           -- soft delete

question_imports(id PK, uploaded_by, upload_sha256, filename, staged_key,
                 row_count, rejected_count, report JSONB, status,
                 created_at, confirmed_at, expires_at)
```

`question_imports` closes a hole in §10.3: the two-phase import has nowhere to keep
its dry-run verdict or the uploaded bytes between the two requests. `staged_key`
points into the private staging bucket (§10.3 of this document).

### 4.2 The read model is written in the same transaction

`games` and `game_players` are **projections of the event log, maintained inside the
same unit of work as the event append** — never by an asynchronous projector, because
ADR-001/4 requires externally visible state to become durable atomically with the
events that produced it.

`tx.append()` therefore also applies:

```
games.last_seq · games.status · started_at · finished_at · winner_id
game_players   INSERT on PlayerJoined
game_players   DELETE on PlayerLeft            ← without this the reducer fix in §3.1
                                                 still collides with UNIQUE(game_id, seat)
game_players.final_score on GameFinished
```

`games.status` mirrors `Phase`, which has no `FINAL`: `lobby · expansion · battle ·
finished · aborted`. `FinalTiebreak` is a `Turn` variant inside `BATTLE`.

### 4.3 Event codec and versioning

```
type            stable wire name — "battle.territory_captured" — held in an explicit
                registry, decoupled from Python class names so a refactor cannot
                rewrite history
schema_version  SMALLINT, per event type
upcast(wire_type, from_version, payload) -> payload
                chains forward on read; decoding only ever targets the current
                dataclass
Decimal         serialized as string; JSON floats would corrupt numeric answers
datetime        ISO-8601, UTC
```

Rename, retype, and remove are permitted **with** an upcaster and a version bump.

**The guard is a golden corpus:** committed raw event rows for several complete
trajectories, asserted both to decode and to fold to an expected final state. That
catches a semantic reducer change, not merely JSON shape drift.

### 4.4 Append

```sql
UPDATE games SET last_seq = :new_seq WHERE id = :gid AND last_seq = :expected;
-- rowcount 0 → ConcurrentModification → quarantine, never retry
INSERT INTO game_events (game_id, seq, operation_id, type, schema_version, payload)
VALUES ...;
```

The `UPDATE` runs first: it takes the row lock and performs §11.2's optimistic check
in one statement.

### 4.5 Migrations

Alembic. CI asserts `alembic check` is clean and that `upgrade head` succeeds from an
empty database. Migrations run as a dedicated one-shot step, never from a reloadable
application entrypoint (§10.5).

---

## 5. Services and runtime

`services/ports.py` declares every capability as a `Protocol`; no implementation
lives under `services/`. That keeps `api → services → domain` one-directional and
makes Spec 1 §12.2's test list (fake clock, breakable broadcaster, breakable commit)
mechanical rather than heroic.

### 5.1 Ports

```python
class Clock(Protocol):
    def now(self) -> datetime: ...
    async def sleep_until(self, when: datetime) -> None: ...
```

`sleep_until`, not `sleep`: §12.2 requires that no test waits on wall-clock time, and
a duration-based API would force the fake clock to reconstruct absolute deadlines the
runtime already computed.

```python
class Broadcaster(Protocol):
    def publish(self, game_id: GameId, base_seq: int, state: GameState,
                events: Sequence[GameEvent]) -> None: ...

class GameSubscriberControl(Protocol):
    def close_game_subscribers(self, game_id: GameId, code: int) -> None: ...
```

`publish` is **synchronous and takes domain objects**. Synchronous because §8.6
forbids the runtime from awaiting a socket write, and a `def` cannot be awaited by
accident. Domain objects because only the WebSocket layer knows each subscriber's
`ViewerContext`, so §8.7's per-viewer projection must happen there.

**Contract, enforced by test rather than by signature:** `publish` may only project
and `put_nowait`. No awaits, no blocking I/O, no network, and no exception escapes.
The §12.2 backpressure test — bounded loop latency against a client that never reads
— is what actually catches blocking work; the signature only prevents `await`.

Further ports: `MediaStore`, `ImportStagingStore`, `QuestionBank`, `GameEventStore`,
`GameRepository`, `UnitOfWork`.

### 5.2 The consumer loop

```
qc = await queue.get()                            # nothing open while waiting
async with uow.begin() as tx:
    ctx    = await materialiser.build(state, qc.command, tx)
    events = decide(state, qc.command, ctx)       # pure, microseconds
    if not events:
        outcome = NoOp()                          # §6.1 ignore
    else:
        await tx.append(game_id, expected_last_seq=state.seq,
                        events=events, operation_id=qc.operation_id)
        outcome = Committed(events)
# COMMIT — every lock released here

if isinstance(outcome, NoOp):
    qc.origin.resolve_noop()                      # no evolve, no reschedule, no publish
    continue

state = fold(evolve, state, events)
reschedule_deadline()
publish()
qc.origin.resolve_ok()
```

**Origins resolve only after the transaction context exits.** No external response is
produced while database locks are held.

**Every origin resolves exactly once** — on ignore, on reject, on success, and on
quarantine. An unresolved `RestOrigin` future is a hung HTTP request; §8.2's
reasoning about unobserved futures applies to the success path too.

**Origin resolution is non-throwing and idempotent.** A REST client can disconnect
while its command is in the queue, leaving a cancelled future whose `set_result`
raises `InvalidStateError` — *after* the batch has already committed. If that
propagated, a delivery failure on a dead HTTP request would quarantine a game whose
state is durable and correct. Every `resolve_*` method therefore swallows and logs
its own failure, and a second call is a no-op. **Transport delivery failure is
logged and never reaches runtime fault handling.** Regression test: cancel a REST
request after its command is enqueued, and assert the command still commits and the
runtime stays healthy.

### 5.3 One transaction per command

Selection and append share one unit of work for *every* command, not as a `StartGame`
special case. `StartGame`'s materialiser runs inside that transaction:

```sql
SELECT q.* FROM questions q
 WHERE q.is_active AND q.kind = :kind
 ORDER BY random() LIMIT :n
   FOR SHARE;
```

Fewer than `:n` rows → `RejectedCommand(QUESTION_POOL_INSUFFICIENT)`, rollback, the
game stays in `LOBBY` — §10.6's authoritative checkpoint, now genuinely
authoritative because the locks are still held when the events are inserted.

Locking only the parent `questions` row suffices **because** §7 mandates that every
semantic edit bumps `questions.version`, which touches that row. This promotes the
version-bump rule from bookkeeping to a locking invariant: an admin path that edited
`question_choices` without bumping the parent would slip past this lock. The admin
service therefore enforces the bump in exactly one place, and a test asserts that a
choice edit changes `version`.

### 5.4 Deadlines

One-shot `asyncio.Task`, cancelled and respawned whenever `current_deadline().id`
changes. It sleeps via `clock.sleep_until` and submits `ExpireDeadline(deadline_id)`
on wake. A stale fire is already harmless under guard 2, so correctness never depends
on cancellation winning a race.

### 5.5 Failure policy

| Condition | Action |
|---|---|
| `RejectedCommand` from `decide` | rollback, reply to origin, state untouched, runtime healthy |
| insufficient bank at `StartGame` | same — an ordinary rejection, not a fault |
| known rollback (`40001`, `40P01`) | bounded retry (3, jittered) — **re-runs `materialiser.build()` and `decide()` in a new transaction**; only `operation_id` is stable |
| ambiguous commit | reconcile by `(game_id, operation_id)` |
| persistence unavailable after retries | quarantine |
| exception in `decide`/`evolve` | quarantine, never retry |
| exception in materialiser (database) | quarantine — a *domain* shortfall is a rejection instead |
| broadcaster raises after commit | log, continue, never quarantine |

**Retry re-runs the whole attempt.** The `FOR SHARE` locks are released at rollback,
so reusing an already-materialised `StartGame` pool would mean selecting under locks
that no longer hold — silently downgrading §10.6 back to advisory. Re-running may
legitimately produce different events (a fresh `ORDER BY random()`, or a rejection if
the bank drained meanwhile); nothing was committed, so that is correct.

**Reconciliation compares the batch, not its existence:**

```sql
SELECT seq, type FROM game_events
 WHERE game_id = :g AND operation_id = :op ORDER BY seq;
```

Verify the exact expected `seq` range (`state.seq + 1 … state.seq + len(events)`),
the row count, and the ordered types against the batch held in memory. A match means
the commit succeeded and processing continues at `fold` with those events. Any
mismatch is quarantine, never "close enough".

**Broadcaster failure never quarantines.** The commit is durable and memory is
correct; destroying a healthy runtime over a misbehaving socket converts a client
problem into a game-wide outage, and §8.5 already gives every client an unconditional
recovery path.

| Broadcast failure | Action |
|---|---|
| outbound queue overflow | close **that** subscriber, `4408` |
| projection or serialization failure | close **that** subscriber, `1011` |

The broadcaster catches everything internally, so no path leaves an open connection
silently stale.

### 5.6 `GameManager`

`GameManager` owns every runtime in the process plus the two background tasks;
`GameRuntime` owns exactly one game.

**Load-once.** `get(game_id)` hits the dict, else takes a per-game `asyncio.Lock`,
re-checks, then loads. Without the lock, two concurrent joins build two runtimes for
one game — ADR-002's divergence failure, in-process.

**Startup recovery.** On boot, load every game with
`status IN ('expansion','battle')`. Not optional: §11.6 forbids evicting active games
because nobody would own their `DeadlineId`, and a restart is exactly an eviction.
Without this, every deploy pauses every live game until a player happens to
reconnect. (`FinalTiebreak` is inside `battle`; there is no `final` status.)

**Generation fencing.** `GameRuntime` carries `generation: int` from a process-global
counter, and `closed: bool`; `submit()` on a closed runtime raises `RuntimeClosed`
and the caller re-`get()`s. Quarantine, **scheduled onto the manager and never run by
the faulting consumer task** — a task cannot cancel and await itself:

```
under the per-game lock:
  detach from the registry
  mark closed
  drain the queue, resolving every origin with GAME_RECOVERING
  cancel the consumer and deadline tasks
  close_game_subscribers(game_id, 1011)     ← via the port; sockets stay owned by the hub
  load a fresh generation
```

§12.2 asserts that nothing queued against R17 ever surfaces in R18.

**Recovery can itself fail, and the registry has a state for that.** Quarantine is
reached *because* something broke — most often persistence — so "immediately load a
fresh generation" is the least likely operation to succeed at that moment. The
registry entry for a game is therefore one of:

```
Live(runtime)          normal
Recovering(attempt_n, next_at)   → callers get GAME_RECOVERING (503)
Failed(reason)                   → callers get GAME_UNRECOVERABLE (503), operator-visible
```

Transient faults — database unavailable, connection refused — retry with bounded
exponential backoff (capped, jittered) and stay `Recovering`. Permanent faults —
a decode failure in the log, an unknown wire type with no upcaster, a
`map_sha256` mismatch — go straight to `Failed` without retrying, because replay
will never succeed and retrying only hides the incident. `Failed` is logged at
error, surfaced in `/api/health/ready` as a degraded detail, and cleared only by
operator action.

**On successful recovery the deadline is honoured, not restarted.** The rebuilt
state carries an absolute `deadline_at` (ADR-003, §11.6): if it is still in the
future the deadline task is scheduled for that instant; if it has already passed,
`ExpireDeadline` is enqueued immediately. Recovery must never extend a window a
player has already spent.

**Watchdog** (§11.5), one task, 5 s tick over resident runtimes: if a current deadline
exists, `now > deadline_at + 5 s`, and no expiry has been *enqueued* for that
`DeadlineId`, enqueue one with a server-generated `operation_id`. Fencing uses
`expiry_enqueued_deadline_id`, not "last expired" — otherwise every tick re-enqueues
while the first expiry is still waiting in the queue.

**Reaper** (§11.6), one task:

```
LOBBY, zero players, older than 5 min             → system abort
LOBBY, older than LOBBY_MAX_AGE_HOURS (default 6) → system abort
LOBBY with no connections                         → runtime may be unloaded
FINISHED / ABORTED                                → unload immediately
EXPANSION / BATTLE                                → never unload, regardless of presence
```

The abandoned-lobby sweep **queries stale rows in the database**, not resident
runtimes, then loads the runtime and submits the system abort. A resident scan would
miss every lobby the no-connections rule had already unloaded, leaving it in the
database forever.

**Bounded queues.** `asyncio.Queue(maxsize=256)`. On full, `submit()` rejects with
`SERVER_BUSY` rather than blocking — the caller is a WebSocket read loop that must not
stall. 256 sits far above any legitimate burst from four players.

**Graceful shutdown.** Stop accepting new commands; cancel watchdog and reaper; then
per runtime: **drain queued commands, resolving their origins with
`SERVER_RESTARTING`**, allow only the already in-flight transaction to finish, cancel
the deadline task, close sockets `1001`. Cancelling mid-`COMMIT` would manufacture the
ambiguous-commit case on every deploy — the one failure mode never worth generating
deliberately.

---

## 6. API

### 6.1 REST surface

```
auth      POST   /api/auth/redeem      {code, username, password, display_name}  public
          POST   /api/auth/login       POST /api/auth/logout
          GET    /api/auth/me

maps      GET    /api/maps             GET /api/maps/{id}

games     GET    /api/games            open lobbies
          POST   /api/games            {preset_id, map_id} → GameSnapshot
          GET    /api/games/{id}       GameSnapshot — §9.3 first paint
          POST   /api/games/{id}/join  POST /api/games/{id}/start

admin     GET  POST         /api/admin/questions
          GET  PATCH        /api/admin/questions/{id}
          POST              /api/admin/questions/{id}/deactivate
          POST              /api/admin/questions/import/dry-run
          POST              /api/admin/questions/import/{import_id}/confirm
          GET               /api/admin/questions/import/{import_id}/rejected.csv
          POST              /api/admin/media
          GET  POST         /api/admin/categories
          PATCH             /api/admin/categories/{id}
          GET  POST         /api/admin/presets
          GET  PATCH DELETE /api/admin/presets/{id}
          GET               /api/admin/presets/{id}/coverage
          GET  POST         /api/admin/invites
          POST              /api/admin/invites/{id}/revoke
          GET               /api/admin/users
          POST              /api/admin/users/{id}/deactivate
          POST              /api/admin/users/{id}/role

health    GET    /api/health/live      GET /api/health/ready
```

`DELETE /api/admin/presets/{id}` is a **soft deactivation** (`is_active = false`) and
returns 409 when the preset is the default. Physical deletion would break historical
`games.preset_id`, which is the same argument §7 already makes for questions.

`GET /api/maps/{id}` returns region ids, display names, and `svg_url` — **never
adjacency**. The client has no use for it, because §8.8 pushes affordances per
viewer, and withholding it keeps the ruleset in `domain/maps` alone.

`GET /api/games` **excludes zero-player lobbies**, which exist transiently by
construction (§6.2).

### 6.2 Game creation

Two commits, deliberately:

```
tx1   INSERT games (status='lobby', last_seq=1)
      INSERT game_events (seq=1, 'game.created', map_sha256=…)
      COMMIT
then  runtime = await manager.get(game_id)
      await runtime.submit(JoinGame(host), origin=RestOrigin(fut))    → seq 2
      return project_snapshot(state, viewer)
```

The host joins **through the runtime**. Writing `PlayerJoined` inside `tx1` would put
seat allocation and the join guards on a second mutation path, which §8.2 forbids —
and seat allocation is precisely the logic §3.1 had to repair, so a single copy of it
matters. The cost is a crash window leaving a player-less lobby, which §5.6's
abandoned-lobby sweep collects after five minutes and which `GET /api/games` hides
meanwhile.

`GameCreated` is unavoidably outside the queue: it is the genesis, and no runtime can
exist before the game does.

### 6.3 Error envelope

One envelope, `{code, message, details?}`, with `code` drawn from a closed enum
shipped through codegen. Exception handlers map **every** source into it:

```
RequestValidationError        422
authentication failure        401
authorization failure         403
not found                     404
method not allowed            405
payload too large             413
RejectedCommand               409  + its RejectCode
SERVER_BUSY                   503
GAME_RECOVERING               503
GAME_UNRECOVERABLE            503
SERVER_RESTARTING             503
database unavailable          503
unhandled exception           500  INTERNAL_ERROR
```

`RejectedCommand → 409` is one case among these, not the privileged one.

The last row is the important one. Starlette's default handlers emit their own
shapes for 404, 405, and unhandled 500s, which would sail straight past the Zod
boundary as unparseable bodies. Catch-all handlers are registered for
`StarletteHTTPException` **and** bare `Exception`, so no route can return a
non-envelope body. A 500 body is **sanitized**: a stable code, a generic message, and
the request id — never an exception message or traceback, which routinely contain
query fragments and connection strings.

`apiFetch` still needs its own typed failure for responses that are not envelopes at
all — a Caddy `502` when the backend is down, an HTML error page, a truncated body.
Those surface as a distinct transport/malformed-response error rather than being
forced into the envelope type, because pretending a proxy error is an application
error loses the one fact that matters: the backend was never reached.

### 6.4 Origin checking

Cookie authentication with no CSRF token makes this load-bearing; `SameSite=Lax`
alone does not cover it.

```
unsafe REST methods   require Origin ∈ ALLOWED_ORIGINS   else 403
/ws handshake         require Origin ∈ ALLOWED_ORIGINS   else 4403
CORS                  disabled
```

All entries in `ALLOWED_ORIGINS` must share a scheme compatible with
`COOKIE_SECURE`; a mixed `http`/`https` list is invalid for a single cookie
configuration and is rejected at startup (§10.4).

### 6.5 The WebSocket hub

`api/ws/hub.py` owns connections. `api/ws/broadcaster.py` implements the
`Broadcaster` and `GameSubscriberControl` ports.

```
Connection   id · AuthenticatedPrincipal · topics{} · outbound Queue(64) · sender task
Hub          connections{} · topic → connection set
```

```python
@dataclass(frozen=True)
class AuthenticatedPrincipal:
    user_id: UserId
    role: UserRole
    session_id: SessionId
```

**A connection stores a principal, not a `ViewerContext`.** The socket is
multiplexed and membership is topic-specific, so one connection can hold different
standing in different topics. A `ViewerContext` is constructed per
`(connection, game)` after membership authorization, and is what projection consumes.

The sender task is the only thing that touches the socket (§8.6). Every `subscribe`
re-authorizes (§8.1) — in Spec 1, participation in that game, else `4403`. Session
revocation closes with `4401`, which is exactly why §7 chose opaque tokens.

**Client frames carry no actor (A-2).** The envelope is
`{command_id, game_id, deadline_id?, type, payload}`; the hub constructs the domain
command with `actor_id = principal.user_id`, and REST does the same from the session.

**Frames are strict.** Every client-frame model is `ConfigDict(extra="forbid")` and
every generated Zod object is `.strict()` — omitting `actor_id` from the schema is
insufficient if extra keys are silently ignored. `deadline_id` exists **only** on the
windowed command variants (`SubmitAnswer`, `PickRegion`, `SelectAttackTarget`), not on
surrender, subscribe, unsubscribe, or ping. `ExpireDeadline` is server-internal and is
never a client frame.

---

## 7. Contracts

```
backend    uv run triviador export-contracts --out ../contracts
             contracts/openapi.json      app.openapi()  — documentation and drift only
             contracts/rest.schema.json  standalone JSON Schema, $defs resolved
             contracts/ws.schema.json    ServerMessage | ClientMessage
             contracts/errors.json       ApiErrorCode + RejectCode

frontend   pnpm codegen        json-schema-to-zod
             shared/api/generated/public.ts   lobby, game, auth, maps
             shared/api/generated/admin.ts    admin DTOs
             shared/api/generated/ws.ts       socket envelope
             shared/api/generated/errors.ts
```

`rest.schema.json` is exported **separately** with resolved `$defs`:
`json-schema-to-zod` cannot consume an OpenAPI document's `components.schemas` as
though the document were JSON Schema. `openapi.json` is retained for documentation
and as a second drift signal.

Generated output is committed; CI regenerates and runs `git diff --exit-code`.

**Zod everywhere at the boundary, including REST.** A malformed REST response parses
as JSON perfectly well and lands in the query cache as typed-but-invalid data, which
no downstream code is prepared for. `apiFetch` parses every response body and every
error body before returning.

**Split modules preserve code splitting.** A single `rest.ts` would pull every
top-level Zod construction into the player bundle regardless of tree-shaking, because
schema construction is a side-effecting top-level expression. `public.ts` and
`admin.ts` are imported only by their respective REST wrappers, and `admin.ts` is
therefore reachable only from the lazy `/admin` route tree.

Admin **form** schemas remain hand-written Zod: they encode UX rules the API schema
does not express well ("exactly 4 choices, exactly 1 correct"), and are kept honest
with `satisfies` against the generated request types.

---

## 8. Frontend

FSD exactly as §9.4, enforced by `steiger`. This section fixes the mechanisms §9 left
open.

### 8.1 Map rendering

Runtime-parsed inline SVG. Fetch `map.svg`, extract region `id` and `d` with
`DOMParser`, render them as real React `<path>` elements. No
`dangerouslySetInnerHTML`: React keeps ownership of fills, strokes, and handlers,
and — decisively — a map stays a **two-file drop with no code change and no rebuild**,
which a build-time SVG-to-component step would destroy.

**One transform contract: flattened, top-level region paths.** Ancestor group
transforms are rejected outright, and `transform` is not in the attribute whitelist.
Supporting "top-level paths *or* composed ancestors" would mean two transform engines
— the validator's and the browser's — with room to disagree; extracting each path's
own transform while ignoring its groups' would silently misplace regions. Source SVGs
are flattened by a normalization step (`svgo` with `applyTransforms`) before they
enter `data/maps`.

The contract, enforced by `maps/validator.py` at build time **and** by the frontend
parser at run time (defence in depth, because the asset is fetched, not bundled):

```
require     root viewBox present and preserved
            region path ids ≡ map.json region ids, exactly — fail closed on any difference
            unique ids
            paths at top level, no transformed ancestor groups
whitelist   id · d · fill-rule · clip-rule
reject      <script> <foreignObject> <use> <image>, href/xlink:href,
            DOCTYPE/entities, transform, any path not matching a region
```

**Region appearance is derived, never stored.** Fill comes from
`territories[id].owner_id` mapped to a per-seat CSS custom property; highlighting
comes from `turn.your_options`. Zustand holds `selectedRegionId` only (§9.2).

### 8.2 The dispatcher

One cache writer, §9.3's `writeGame` with its `seq` comparison, living in `app/`.
`msg.state` goes to the query cache; `msg.events` go to the ephemeral bus; the bus
never writes to the cache.

The gap rule (A-3):

```
base_seq == last_seq              apply state, emit narration events
seq <= last_seq                   duplicate — ignore
seq > last_seq, base mismatch     apply full state, suppress events, advance last_seq
```

Because every update carries full state, the third case costs an animation, not
correctness, and does not require a resync. Resync remains necessary only after a
reconnect (§8.5).

### 8.3 Timer

Rendered from `deadline_at` plus the ping/pong offset (§8.6), driven by
`requestAnimationFrame`, disabling input at the locally computed deadline.
Presentation only — the server's `ctx.now >= deadline_at` stays authoritative.

### 8.4 Routing

TanStack Router, file-based. `beforeLoad` guards read `["me"]`; a 401 anywhere
redirects to `/login`. Screens per §9.7.

---

## 9. Admin

`/admin/*` is a **lazily-loaded route tree** guarded on `role === 'admin'`, so players
never download it and never construct its schemas. Screens follow §10 one-to-one:
question list with server-side pagination and filters; editor with media upload;
two-phase import; invites; users; presets with the `required_question_budget`
coverage readout and its explicit note that editing a preset does not affect running
games.

### 9.1 Two buckets, two ports

```
triviador-media     website-enabled, anonymous read     → MediaStore
                    normalized WebP only                   put · open · delete · list
triviador-staging   private, backend key only           → ImportStagingStore
                    lifecycle expiry                       put · open · delete
```

Not one port with a `staging/` prefix. The security boundary is the bucket, and a
prefix bug in the wrong direction publishes raw import uploads — answers included —
to an anonymously readable endpoint. Their lifecycles differ too: media is immutable
until unreferenced, staging is expiring by design.

Media content is immutable; media *objects* are not retained forever. An unreferenced
object remains deletable by `media-gc` under §10.4's two-way reference check.

### 9.2 Media pipeline

Per §10.4, with two additions.

`Cache-Control: public, max-age=31536000, immutable` is set as **S3 object metadata at
PUT time**, so Garage returns it on 200/206 and, correctly, not on a 404. A blanket
proxy header would attach a one-year cache lifetime to error responses.

WebP re-encoding is CPU-bound and runs in `asyncio.to_thread` behind a semaphore of
one. A 200-image bulk import shares a process with live games (ADR-002), and
unbounded decoding there stalls command processing for every match in flight.

### 9.3 Import

**Dry-run invariant, stated precisely:**

> Dry-run persists staging metadata (`question_imports`) and the original upload (the
> staging bucket). It writes **no** categories, questions, choices, numeric answers,
> media assets, or public media objects.

**Confirm, ordered:**

```
read staged object, recompute sha256
compare recomputed sha256 against question_imports.upload_sha256   ← stored at dry-run
validate + re-encode media
write public blobs (idempotent, content-addressed)
BEGIN
  SELECT ... FROM question_imports WHERE id = :id FOR UPDATE
  recheck status = 'validated', rejected_count = 0, sha match
  INSERT media_assets, questions, choices, numeric
  UPDATE status = 'confirmed'
COMMIT
```

The client's claimed sha is untrusted and is never a gate; the comparison is
recomputed-staged-object against dry-run-stored. Concurrent confirms duplicate
preprocessing and blob writes, which is safe by content addressing; the second loses
at `FOR UPDATE` and returns 409.

**Expiry is a retryable state machine, not an atomic delete.** PostgreSQL and Garage
share no transaction, so "delete the row and the object together" cannot be
implemented. Deleting the row first strands an untracked raw upload — full of correct
answers — in the staging bucket; deleting the object first leaves a row that still
looks confirmable but whose upload is gone. The order is therefore:

```
BEGIN  UPDATE question_imports SET status='expired' WHERE ... AND now() > expires_at
COMMIT                                   ← the row can no longer be confirmed
delete the staged object (idempotent, safe to repeat)
BEGIN  UPDATE ... SET status='cleaned', staged_key=NULL   COMMIT
```

Every step is retryable and a crash anywhere leaves a state the next `media-gc` run
resumes from. `expires_at` is set at dry-run from `IMPORT_TTL_HOURS`. The same
sequence retires the staged object of a **confirmed** import, whose row is kept as an
audit trail with `staged_key = NULL`.

**After a restore, all non-confirmed imports are explicitly expired** regardless of
`expires_at`: staging is deliberately not backed up (§10.9), so their staged objects
are gone and any row still in `validated` would otherwise offer a confirm that must
fail.

---

## 10. Infrastructure

### 10.1 Compose

```
compose.yaml          db · garage · garage-init · migrate
compose.dev.yaml      backend(dev) · frontend(vite)
compose.prod.yaml     backend(prod) · caddy
```

Dependency graph:

```
db      healthy   ─┐
                   ├─→ migrate  completed ─┐
garage  healthy ──→ garage-init completed ─┴─→ backend ready ─→ caddy
```

**`migrate` is a dedicated one-shot service.** It must not live in the reloadable
backend entrypoint, where a dev reload would re-run it and a crash loop would re-run
it repeatedly.

| Service | Dev | Prod |
|---|---|---|
| `db` | `postgres:17-alpine`, `pg_isready` healthcheck, `pgdata` volume | same |
| `garage` | pinned digest, rendered config, meta + data volumes | same |
| `garage-init` | one-shot, idempotent | same |
| `migrate` | one-shot `alembic upgrade head` | same |
| `backend` | bind-mounted `./backend`, **anonymous volume over `.venv`**, `uv run fastapi dev --host 0.0.0.0` | built image, `uvicorn --workers 1` |
| `frontend` | bind-mounted, **anonymous volume over `node_modules`**, `pnpm dev --host`, `./data/maps` mounted at `/app/public/maps` | built at image build; static output served by Caddy |
| `caddy` | — | `:80`, the single origin |

The anonymous volumes are load-bearing: without them the host bind mount shadows the
container's `.venv` and `node_modules`, and both fail in ways that read as dependency
bugs. File watching works because the repository sits on ext4 under `/home`; the
inotify problem is `/mnt/c`, which this project is not on.

**Single origin.** In dev it is the Vite dev server (`:5173`), which proxies `/api`
and `/ws` to the backend **and `/media/*` to Garage's web endpoint with the bucket
Host header** — without that last rule every question image 404s in development. In
prod the single origin is Caddy on `:80`.

**`svg_url` is `/maps/{map_id}/map.svg`, served as a static asset in both
environments and never through the backend:** in dev by mounting `./data/maps` into
the frontend container's `public/maps`, in prod by Caddy from the mounted data volume.
Routing it through the backend would put file streaming on the single authoritative
worker, which is the same objection that keeps media out of it.

### 10.2 Caddy

```caddy
:80 {
    encode gzip zstd

    handle_path /media/* {
        reverse_proxy garage:3902 {
            header_up Host triviador-media.web.garage.internal
        }
    }
    handle_path /maps/* {
        root * /srv/maps
        file_server
    }
    handle /api/* { reverse_proxy backend:8000 }
    handle /ws    { reverse_proxy backend:8000 }

    handle /assets/* {
        root * /srv
        header Cache-Control "public, max-age=31536000, immutable"
        file_server
    }
    handle {
        root * /srv
        header Cache-Control "no-store"
        try_files {path} /index.html
        file_server
    }
}
```

The cache policy is split across two **mutually exclusive `handle` blocks**, matched
on the incoming path, rather than by `header` matchers inside one block. A single
block would make correctness depend on whether Caddy evaluates `header` before or
after `try_files` rewrites the URI — and if it evaluates after, every deep link is
served as `/index.html` and would silently inherit whichever policy matched that
name. Two blocks are order-independent: hashed output under `/assets/*` is
`immutable`, and everything else, **including every SPA fallback response**, is
`no-store`. This assumes Vite's default `build.assetsDir = "assets"`; changing it
requires changing the matcher.

`handle_path` strips the prefix; the `header_up` is **mandatory**, because Caddy
otherwise forwards the client's `Host` (a bare LAN address) and Garage cannot resolve
a bucket from it. Proxying to `:3900` instead would demand SigV4 on every image.

Hashed Vite assets get `immutable`; `index.html` gets `no-store`, so a deploy is
picked up without a hard refresh.

### 10.3 Garage

Pinned to an **exact version or digest**, never a floating `:v2` tag: `garage-init`
depends on CLI syntax, and a silent image bump would break bootstrap at the worst
moment.

```toml
replication_factor = 1
metadata_dir       = "/var/lib/garage/meta"
data_dir           = "/var/lib/garage/data"
db_engine          = "lmdb"
rpc_bind_addr      = "[::]:3901"
rpc_public_addr    = "garage:3901"
rpc_secret_file    = "/run/secrets/garage_rpc_secret"

[s3_api]
api_bind_addr = "[::]:3900"
s3_region     = "garage"

[s3_web]
bind_addr   = "[::]:3902"
root_domain = ".web.garage.internal"

[admin]
api_bind_addr = "[::]:3903"
```

**Garage does not interpolate environment variables inside a mounted TOML file.** The
config is rendered from a template before launch, and the RPC secret is supplied via
`rpc_secret_file` rather than inline.

The backend's S3 client uses **path-style addressing** against
`http://garage:3900` — virtual-host style would require per-bucket DNS that does not
exist inside the compose network.

`garage-init`, idempotent, every step guarded:

```
wait healthy → ensure single-node layout applied
garage key import <S3_ACCESS_KEY_ID> <S3_SECRET_ACCESS_KEY> --yes
garage bucket create triviador-media
garage bucket create triviador-staging
garage bucket allow --read --write triviador-media   --key triviador-backend
garage bucket allow --read --write triviador-staging --key triviador-backend
garage bucket website --allow triviador-media            ← media only
assert triviador-staging has website disabled            ← fail the job otherwise
```

The key is **imported**, not created, so credentials come from configuration and stay
stable across rebuilds instead of being generated inside a container. The closing
assertion is the one that matters: a staging bucket that ever becomes website-enabled
publishes raw import uploads, answers included.

### 10.4 Configuration and secrets

`pydantic-settings`, environment variables, `.env.example` committed, `.env`
gitignored (already is) at mode 0600.

```
DATABASE_URL · ALLOWED_ORIGINS · ALLOWED_HOSTS · COOKIE_SECURE=false · SESSION_TTL_DAYS
S3_ENDPOINT · S3_REGION · S3_ACCESS_KEY_ID · S3_SECRET_ACCESS_KEY
S3_MEDIA_BUCKET · S3_STAGING_BUCKET · MEDIA_PUBLIC_BASE=/media
MAPS_ROOT · LOG_LEVEL · LOG_FORMAT · LOBBY_MAX_AGE_HOURS · IMPORT_TTL_HOURS
POSTGRES_PASSWORD · GARAGE_RPC_SECRET
```

No secret manager: a `.env` on a LAN box is the honest answer at this scale. Two
startup assertions instead, so an unconfigured deploy fails loudly rather than
running with a published password:

1. every `ALLOWED_ORIGINS` scheme is consistent with `COOKIE_SECURE`
2. no secret still holds its `.env.example` placeholder

**Hostname note.** A DHCP reservation stabilizes the box's address but does **not**
make `triviador.local` resolve; that requires router DNS, `/etc/hosts` entries, or
mDNS (avahi). Cookies are host-only, so signing in via the IP and via the hostname
produces two independent browser sessions. Both may appear in `ALLOWED_ORIGINS`, but
players should be told one address.

### 10.5 Startup order

```
migrate service:  alembic upgrade head   (under pg_advisory_lock)
       ↓
backend:          startup recovery — load runtimes for status IN ('expansion','battle')
       ↓
                  serve; readiness flips true
```

ADR-002 already guarantees one application process, so the advisory lock exists only
to stop a stray manual `alembic` from racing a deploy. Recovery runs strictly after
migrations: rebuilding state against an old schema is how a "successful" deploy
silently corrupts live games.

### 10.6 Health

```
GET /api/health/live    process and event loop only — never touches the database
GET /api/health/ready   database reachable · migrations current · startup recovery
                        complete · Garage initialization verified
                        · any game in Failed reported as a degraded detail
```

Readiness reports the *result* of the startup Garage assertion rather than probing
Garage on every poll; a liveness probe that depends on the database restarts a healthy
process during a database blip.

### 10.7 CI (GitHub Actions)

| Job | Gate |
|---|---|
| `backend` | `ruff check` · `ruff format --check` · `mypy --strict` on `domain`+`services` · `pytest` against a Postgres service container · **100 % branch on `domain/game/reducer.py`** · golden event corpus |
| `frontend` | `biome` · `steiger` · `tsc --noEmit` · `vitest` |
| `contracts` | `export-contracts` + `pnpm codegen` → `git diff --exit-code` |
| `maps` | validator over `data/maps/*` — `map.json` topology **and** the `map.svg` whitelist |
| `migrations` | `alembic check` clean · `upgrade head` succeeds from an empty database |
| `compose` | `docker compose config` validates for both the dev and prod overlays |
| `e2e` | prod compose up; one Playwright scenario — 3 contexts, redeem → create → join → start → `FINISHED` on exp 1 / battle 1 |

The E2E seed includes **at least one media question**, so the run exercises
Caddy → Garage delivery rather than only the API.

### 10.8 Backups

A **Windows scheduled task** invoking a **pinned Compose backup service** inside WSL,
nightly (§10.12 explains why not a systemd timer):

```
flock /var/lock/triviador-media.lock          ← media-gc binds the identical host path
  1. pg_dump -Fc                        → backups/db/<ts>.dump
  2. rclone copy garage:triviador-media → backups/media/     (append-only)
  3. verify: pg_restore --list on the dump succeeds
             rclone check garage:triviador-media backups/media/ --one-way
  4. retention: 7 daily, 4 weekly dumps
```

The verification step was previously stated as "every object named in the dump's
manifest" — but nothing generates such a manifest, and `pg_dump -Fc` does not contain
one. `rclone check --one-way` proves the property that actually matters: every object
currently in Garage exists in the append-only backup. It runs **inside the same
`flock`** as the dump and the copy, so `media-gc` cannot delete an object between the
copy and the check and turn a healthy backup into a spurious failure.

**`copy`, not `sync`.** A `sync` maintains one mutable mirror, so a retained weekly
dump can reference an asset a later run deleted. Because object keys are
content-addressed, `copy` deduplicates naturally and never removes an asset an older
dump still needs.

`pg_dump` runs first so media is always a **superset** of what the snapshot
references. Verification therefore asserts manifest coverage, not equal object counts.

The backup service and `media-gc` must bind the **same host lock path**, so their
`flock` calls resolve to the same inode; two container-local paths would not exclude
each other, and `media-gc` would happily delete blobs a running backup still needs.

Backups land on a different disk or a NAS path. Single-node Garage has
`replication_factor = 1` and therefore zero redundancy, so this copy is the only
redundancy in the system.

### 10.9 Restore drill

Written down, and exercised once before this is relied on:

```
1. start fresh db + garage
2. run garage-init
3. restore the database (pg_restore)
4. restore media, re-applying Content-Type and Cache-Control object metadata
5. run migrations required by the current application
6. expire every non-confirmed import (§9.3) — their staged objects were not backed up
7. start backend and caddy
```

Verification covers all three failure surfaces: a **finished game replays** from the
log, an **active game with a persisted deadline** resumes and expires at its original
absolute time, and a **question image loads** through Caddy → Garage.

Staging imports are deliberately not backed up; an unfinished import simply becomes
expired after a restore.

### 10.10 Logging

`structlog` JSON to stdout, `json-file` driver with `max-size` and `max-file`
rotation. Every command logs `game_id`, `operation_id`, command type, committed `seq`
range, and `duration_ms`; every HTTP request logs a request id.

Logged unconditionally at `info`: quarantine, watchdog firing, reaper aborts,
backpressure closes, migration runs, and the startup recovery count — those six are
the whole story of a bad night.

**Logs must never contain** answers or answer values, command payloads, passwords,
cookies, session tokens, invite codes, or S3 credentials. A redaction test asserts
this against a captured log stream, for the same structural reason §12.3 rejects
byte-scanning: a correct answer is legitimate text elsewhere, so the guarantee has to
be about which fields are emitted.

### 10.11 LAN exposure, hardening, and lifecycle

**Only Caddy is published.** Production publishes `0.0.0.0:80:80` and nothing else —
not PostgreSQL, not the backend, not Garage's S3 (`3900`), web (`3902`), or admin
(`3903`) listeners, not `migrate`. Services reach each other over the compose network,
which needs no published port. Development ports bind to `127.0.0.1` unless another
device is deliberately being tested against.

Garage's admin listener is the sharpest of these: it is an unauthenticated control
plane on the compose network, and publishing it would hand any LAN device the ability
to rewrite bucket permissions — including turning the private staging bucket into a
website.

**Host is validated as well as Origin.** §6.4 covers `Origin`; a LAN service also
needs `Host` checked, or a DNS-rebinding page on a player's browser can reach it from
outside. Two layers:

```
FastAPI   TrustedHostMiddleware over ALLOWED_HOSTS
Caddy     a host allowlist at the edge
```

**Deployment is one deterministic command.** Compose does **not** reliably re-run a
completed one-shot container just because the code inside it changed, so a deploy that
only does `up -d` can silently skip a migration:

```
docker compose run --rm garage-init
docker compose run --rm migrate
docker compose up -d --remove-orphans
```

Everything still runs in containers. This wrapper is the only supported way to deploy.

**Process lifecycle.**

```
long-running (db, garage, backend, caddy)   restart: unless-stopped
one-shot (garage-init, migrate, backup)     restart: "no"
backend                                     stop_grace_period > DB statement timeout
```

The grace period is load-bearing rather than cosmetic: §5.6's graceful shutdown lets
the in-flight transaction finish precisely so a deploy never manufactures the
ambiguous-commit case, and a grace period shorter than the statement timeout kills the
container mid-`COMMIT` and manufactures it on every deploy instead.

**Host clock synchronization is a correctness requirement.** ADR-001/5 persists
absolute deadlines, so a wrong host clock does not degrade the game, it corrupts it —
every open window expires at the wrong instant, and recovery computes the wrong
remaining time. The host runs time sync, containers run UTC, and a large clock
correction is an operational incident, not a curiosity.

**Live data stays on local storage.** PostgreSQL's data directory and Garage's
metadata and data directories must sit on a local Linux filesystem — never SMB, NFS,
or a NAS mount. Both assume POSIX locking and `fsync` semantics that network
filesystems do not reliably provide, and the failure mode is corruption, not slowness.
The **backup destination** may be a NAS or a separate disk; the live volumes may not.

**Edge restriction.** Where practical, bind Caddy to the reserved LAN address rather
than every interface, and allow inbound `80` only from the intended subnet.

**Security note — plain HTTP is a trust assumption, not an oversight.** Passwords and
session cookies cross the LAN in cleartext and are readable by any device able to
sniff it. That is acceptable for a trusted home or classroom network and nothing else.
If guests or untrusted devices share the network, the answer is Caddy's internal TLS
or a private overlay network — not a tweak to the cookie flags.

### 10.12 WSL2 host platform

Production runs on Docker Desktop with the WSL2 backend, which is a different
operating environment from generic Linux Docker in ways that reach the deployment
design.

**Networking.** Docker Desktop forwards published ports through Windows, which makes
`80` reachable from the LAN without WSL mirrored-mode networking. Nothing else is
published (§10.11). An inbound Windows Defender Firewall rule for TCP 80 is required,
scoped to **LocalSubnet**; the traffic is owned by `com.docker.backend.exe`, not by a
Linux process. The Windows machine takes a DHCP reservation, and that address — plus
an optional hostname — goes into `ALLOWED_ORIGINS` and `ALLOWED_HOSTS`.

**Startup and unattended recovery.** Docker Desktop is tied to the Windows user
session, so "the server is up" depends on someone being signed in. Enable *Start
Docker Desktop when you sign in*, and add a Windows Task Scheduler job that waits for
the Docker engine and then runs the §10.11 deployment command. Without it, a reboot
leaves the games down until a human notices.

**Backups are scheduled by Windows, not by systemd.** WSL's own documentation is
explicit that systemd services do not keep a WSL instance alive, so a systemd timer
inside WSL is not a dependable always-on scheduler. §10.8's job is therefore a Windows
scheduled task that invokes the backup script inside WSL.

**Storage placement.**

```
repository                     /home/... inside the WSL filesystem — never /mnt/c
postgres + garage volumes      Docker Desktop's Linux-side named volumes
backups                        OUTSIDE the WSL/Docker virtual disk — a Windows disk,
                               an external drive, or a NAS
```

The backup rule is the one that matters. Live data and backups both living inside the
same `ext4.vhdx` means a single corrupted virtual disk destroys the data and its only
copy simultaneously, which is not a backup at all. The repository placement is also
why file watching works in dev (§10.1): `/mnt/c` is where inotify breaks.

**Sleep and hibernation are disabled while hosting.** Windows sleeping suspends WSL,
which pauses the backend mid-match. Recovery is correct on wake — overdue deadlines
expire immediately per §5.6 — but every connected game is disrupted, so the correct
handling is prevention, not recovery.

**Docker Desktop must stay in Linux-container mode** with WSL integration enabled for
the distribution holding the repository.

**Honest caveat:** WSL2 is adequate for this deployment's scale and audience, but it
is not an unattended server environment by default. Every item above exists because
some part of "the machine is running" is a Windows concern rather than a container
concern.

---

## 11. Testing additions

Layers 1 and 2 are Spec 1 §12.1–12.2 plus:

```
domain      88-cell transition matrix, including EXPANSION/MediaWarmup
            seat allocation: join → leave-from-middle → join yields distinct seats
            genesis: create_initial_state; evolve(GameCreated) raises
            warmup: no question deadline is open during MediaWarmup

runtime     retry re-runs materialiser + decide, and a StartGame retry re-selects
            reconciliation rejects a batch whose seq range does not match exactly
            quarantine teardown runs off the faulting task
            watchdog does not double-enqueue while an expiry is queued
            reaper aborts an unloaded abandoned lobby found only in the database
            shutdown drains with SERVER_RESTARTING and lets the in-flight tx finish
            a REST request cancelled after enqueue still commits, runtime stays healthy
            a raising reply sink never reaches fault handling
            quarantine with persistence down enters Recovering and retries with backoff
            a permanent replay failure enters Failed without retrying
            recovery honours an absolute deadline: future → scheduled, past → expired now

codec       golden corpus decodes and folds to the expected final state
            upcaster chain reaches the current version
```

Layer 3 keeps §12.3's four contract tests and adds:

```
strictness      an extra field on any client frame is rejected
actor rejection a frame carrying actor_id is rejected outright (extra="forbid")
actor derivation an actorless valid frame builds the domain command from the principal
envelope        validation · auth · authz · 404 · 405 · 413 · 409 · 503 · unhandled 500
                all use the one envelope, and a 500 body carries no exception text
origin          a foreign Origin is refused on unsafe REST and on the WS handshake
logs            redaction of answers, payloads, credentials, and tokens
```

The earlier formulation — "a frame naming another player still acts as the session's
user" — contradicted strict validation: `extra="forbid"` rejects that frame before
any actor could be derived from it. The two properties are separate and both are
worth asserting: the field is unacceptable, *and* identity comes from the principal.

Frontend, boundary invariants only — never CRUD (§12.5):

```
REST Zod rejection of a malformed response
transport error for a non-envelope response (502 / HTML / truncated body)
dispatcher sequencing and all three base_seq cases
SVG parser: whitelist, viewBox, rejection of transformed ancestors, region matching
media warmup precedes the first question deadline
```

Actor identity is asserted once, in the API/WS layer where it is enforced — not
duplicated here, since the frontend simply never constructs the field.

Layer 4 remains exactly one Playwright scenario (§12.4), now seeded with a media
question.

---

## 12. Plan sequence

```
Plan 2  domain amendments   seat allocation · genesis + map_sha256 · system abort
                            · MediaWarmup + warmup_ms · 88-cell matrix
Plan 3  persistence         models, read model, repositories, Alembic, event store,
                            codec + upcasters + golden corpus
Plan 4  runtime             GameManager, GameRuntime, ports, recovery, quarantine,
                            watchdog, reaper, shutdown
Plan 5  api + contracts     REST, /ws hub, projection, export-contracts, codegen
Plan 6  frontend core       shell, auth, lobby, game screen, map, dispatcher
Plan 7  admin               question bank, import, media, invites, presets, users
Plan 8  infrastructure      compose, Caddy, Garage, config, CI, backups, E2E smoke,
                            LAN exposure + hardening, WSL2/Windows host wiring
```

Plan 2 leads because it is pure, fast, and every later plan builds on the corrected
shapes. Plans 3 and 4 stay separate because the runtime's hard parts — quarantine,
ambiguous commit, watchdog fencing — are only testable against a real event store.

---

## 13. Open items

1. **`map.svg` for Czechia is still unsourced** (Spec 1 §14.1). It must satisfy §8.1's
   flattened-path contract after `svgo` normalization, and its licence must permit
   redistribution and be recorded in `data/maps/czechia/LICENSE`.
2. **`steiger` rule set** (Spec 1 §14.2) — settled during Plan 6.
3. **Seed question bank** (Spec 1 §14.3) — at minimum 17 numeric + 12 multiple-choice
   active questions, or the default preset cannot start a game.
4. **Garage CLI syntax must be verified against the pinned image** during Plan 8;
   `garage-init` is written against one exact version.
5. **Backup destination path** — which Windows disk, external drive, or NAS path holds
   `backups/` (§10.12 forbids the WSL virtual disk), decided at deployment.
6. **LAN reachability must be verified from a second device**, not assumed: published
   port, Windows firewall scope, `ALLOWED_ORIGINS`/`ALLOWED_HOSTS`, and WebSocket
   upgrade through Caddy. Guest-Wi-Fi and client-isolation behaviour needs the same
   check — AP isolation silently breaks peer reachability while the host looks healthy.
7. **Reboot recovery must be rehearsed once**: reboot Windows, confirm Docker Desktop
   autostarts, the scheduled deployment task runs, and an active game recovers with its
   deadlines intact (§5.6).
