# Triviador Online — Spec 1: Playable Core

**Date:** 2026-08-07
**Status:** Approved design, ready for implementation planning
**Scope:** Spec 1 of 2. Spec 2 (admin depth) is defined in §13.

---

## 1. Purpose and scope

An online implementation of Triviador — a trivia game where 2–4 players conquer
territories on a region map by answering questions — for an invite-only private
group (friends, colleagues, a class). Players are trusted; there is no
anti-cheat, no public matchmaking, no ranking economy.

Spec 1 delivers a complete, playable product:

- accounts gated by admin-issued invite codes
- one data-driven map
- an authoritative game engine implementing the full ruleset
- realtime play over WebSocket with reconnect
- the game UI
- the admin surface required to *operate* the game: question bank, invite codes,
  rule presets

Spec 2 adds admin depth (match history, replay, analytics, live spectate and
intervention). It is deliberately deferred because it is much cheaper to build
on top of the event log Spec 1 produces.

---

## 2. Architecture decision records

These are invariants, not preferences. Violating one is a defect.

**ADR-001 — Core invariants**

1. Server state is authoritative. The client never computes game state.
2. Commands are processed sequentially per game, through one queue.
3. The domain layer performs no I/O.
4. Events are committed to PostgreSQL before any externally visible state is published.
5. Timers are persisted absolute deadlines, never `sleep` durations.

**ADR-002 — Single worker**

The backend runs with exactly one worker process (`uvicorn --workers 1`).
Authoritative game state lives in that process's memory. This is an
architectural invariant, not a configuration choice: a second worker would hold
a second, divergent copy of a game. Horizontal scaling would require game
ownership plus a message broker, which the expected scale does not justify.

**ADR-003 — Disconnect does not affect rules**

Timers keep running while a player is disconnected. Presence is a runtime
concern; no rule depends on it. Consequently, timer tasks are owned by
`GameRuntime` and never by a connection or subscription lifecycle, and an active
game's runtime is never evicted from memory (§11.4).

**ADR-004 — The event log is the truth; memory is a disposable cache**

Any uncertainty about in-memory state is resolved by discarding the runtime and
rebuilding it from the log. Partially applied state never survives an error.

---

## 3. Game rules

### 3.1 Shape of a match

Two stages on a map of ~15 regions.

| Stage | Rounds | What happens |
|---|---|---|
| Expansion | `expansion_rounds` (max) | All players answer a numeric "guess closest" question. Players claim free regions in rank order. |
| Battle | `battle_rounds` | Each player, in turn order, attacks one adjacent region. |

Each player starts with a **base**: a 3-tower castle on a randomly drawn region.
Bases are never adjacent to one another.

### 3.2 Configurable rules (`GameRules`)

```python
@dataclass(frozen=True)
class GameRules:
    player_count: int              # 2..4
    expansion_rounds: int          # maximum, not exact
    battle_rounds: int
    base_hp: int                   # default 3
    answer_timeout_ms: int         # default 20_000
    pick_timeout_ms: int           # default 15_000
    claims_by_rank: tuple[int, ...]   # e.g. (2, 1, 0); len == player_count
    pts_base: int                  # 1000
    pts_territory: int             # 200
    pts_conquered: int             # 400
    pts_defense: int               # 100
```

A game stores a **frozen copy** of its rules in `games.rules`. Editing a preset
never changes a game already in flight.

### 3.3 Expansion

Each round: one numeric question to all active players. Ranking is by
`|guess − correct|` ascending, ties broken by `elapsed_ms` ascending;
non-answerers rank last, ordered among themselves by seat for determinism.

`claims_by_rank[i]` grants are issued to rank *i*, truncated to the number of
regions actually free. **Adjacency is not required in expansion** — any free
region may be claimed.

The stage ends when the round budget is exhausted **or** no free regions remain,
whichever comes first. Any regions still free remain **neutral** and are
attackable during the battle stage.

This makes any player count work on any map, which a fixed
`player_count + rounds × sum(claims) == region_count` constraint would not.

**Pick timeout:** auto-pick a random free region (from materialised
`ctx.shuffled_region_ids`), recorded with `automatic=True`. A forfeited pick
would leave holes in the map and degrade the match for everyone.

**One pick = one window.** A player granted two picks gets two separate
`DeadlineId`s, not one deadline covering both. Otherwise a player can spend
14.9 s on the first pick and leave themselves no time for the second.

### 3.4 Battle

On their turn a player selects one region adjacent to a region they own and not
owned by themselves. Two distinct resolution paths:

**Neutral target** (`owner is None`) → `NeutralChallenge`. Only the attacker
answers a multiple-choice question.

```
correct         → NeutralTerritoryCaptured, +pts_territory
wrong / timeout → NeutralAttackFailed
```

No defender, no base damage, no defense points.

**Owned target** → `BattleDuel`. Both attacker and defender answer a
multiple-choice question.

```
attacker ✓, defender ✗  → attacker wins  → capture branch
attacker ✗, defender ✓  → defender wins  → DefenseHeld, +pts_defense
attacker ✗, defender ✗  → turn over, no change
attacker ✓, defender ✓  → BattleTiebreak (numeric question)
```

Tiebreak: closer guess wins; equal distance → faster wins; both silent →
**defender holds** (defender advantage). Then the capture branch.

**Capture branch:**

```
normal region   → TerritoryCaptured(acquisition=CONQUEST)
                  ScoreChanged(attacker, +pts_conquered, CONQUEST)
                  ScoreChanged(defender, −old_holding_value, TERRITORY_LOST)

base, hp > 1    → BaseDamaged(hp − 1)          # region does not change hands
base, hp == 1   → BaseDestroyed
                  TerritoryCaptured(base → attacker, acquisition=BASE)
                  ScoreChanged(attacker, +pts_base, BASE)
                  ScoreChanged(defender, −pts_base, BASE_LOST)
                  PlayerEliminated(defender)
                  for each remaining region r of defender:
                      TerritoryNeutralized(r)
                      ScoreChanged(defender, −value(r), TERRITORY_LOST)
```

The destroyed base transfers to the attacker; **every other holding of the
eliminated player becomes neutral**, not inherited. Automatic inheritance would
create a runaway positive-feedback loop in which one base kill decides the
match.

Accumulated bonuses are **never** removed by elimination. A player who loses
everything but earned 300 defense points finishes with 300.

### 3.4.1 Degenerate cases

**No legal target.** A player whose territories border nothing they can attack
(everything adjacent is already theirs) has no move. `BattleTargetSelect` is
never entered with empty `your_options`: `decide` emits `TurnSkipped(player)`
immediately and advances to the next attacker. A player must never be shown a
window in which no action is legal.

**Surrender mid-turn.** `Surrender` is legal in any non-terminal phase. It emits
`PlayerSurrendered`, `PlayerEliminated`, and the same territory-neutralisation
sequence as base destruction — except that the surrendering player's base is
neutralised too, since no attacker earned it. If the surrendering player was the
current actor or the defender of an open duel, the current turn is cancelled
(`TurnAborted`) and play advances to the next attacker; the open question is
discarded without resolution. Cancelling the whole turn is simpler and less
exploitable than trying to award the contested region.

**Bases must be placeable.** `BasesAssigned` requires `player_count` mutually
non-adjacent regions. The map validator (§4) asserts that the adjacency graph
contains an independent set of size ≥ 4 (the maximum supported `player_count`),
so this can never fail at runtime for a registered map.

### 3.5 Scoring

```
score(player) = Σ holding_value(territory, rules) for owned territories
              + accumulated non-territory bonuses
```

`holding_value` is derived from `Territory.acquisition`, not from region type,
because the same region is worth 200 to a player who claimed it in expansion and
400 to whoever later conquers it:

```python
class AcquisitionKind(StrEnum):
    CLAIMED = "claimed"      # taken while unowned — expansion pick or
                             # neutral challenge
    CONQUEST = "conquest"    # taken from another player
    BASE = "base"


def holding_value(t: Territory, rules: GameRules) -> int:
    match t.acquisition:
        case AcquisitionKind.CLAIMED:  return rules.pts_territory
        case AcquisitionKind.CONQUEST: return rules.pts_conquered
        case AcquisitionKind.BASE:     return rules.pts_base
        case None:                     return 0
```

`CLAIMED` rather than `EXPANSION`, because an unowned region can also be taken
during the battle stage via a neutral challenge; both are worth
`pts_territory`. Values are deliberately asymmetric: a region worth 200 to its
previous owner becomes worth 400 to its conqueror.

`ScoreChanged` is a first-class event carrying `(player_id, delta, reason,
new_total)`. Scoring consequences are **never** embedded into gameplay events,
because one gameplay event can produce several score effects (or, under a
different preset, none), and because analytics must be able to read scoring
history without knowing which rules version produced it.

```python
class ScoreReason(StrEnum):
    BASE = "base"
    TERRITORY = "territory"
    CONQUEST = "conquest"
    DEFENSE = "defense"
    TERRITORY_LOST = "territory_lost"
    BASE_LOST = "base_lost"
    BONUS = "bonus"
```

### 3.6 End of game

The game finishes when the battle round budget is exhausted or only one active
player remains. Highest score wins. A score tie is broken by a final numeric
question (`FinalTiebreak`).

---

## 4. Repository layout and stack

```
triviador/
├── backend/
│   ├── pyproject.toml                 # uv
│   └── src/triviador/
│       ├── domain/                    # PURE — no I/O, imports nothing below
│       │   ├── game/
│       │   │   ├── state.py           # GameState, Turn, Territory, PlayerState
│       │   │   ├── actions.py         # Command types
│       │   │   ├── events.py          # GameEvent types
│       │   │   ├── reducer.py         # decide() / evolve()
│       │   │   ├── scoring.py         # holding_value, budgets
│       │   │   └── rules.py           # GameRules, required_question_budget
│       │   ├── questions/             # QuestionSnapshot, kinds, budget types
│       │   └── maps/                  # MapDefinition, adjacency queries
│       ├── services/
│       │   ├── games/
│       │   │   ├── manager.py         # GameManager: registry, watchdog
│       │   │   ├── runtime.py         # GameRuntime: queue, timers, broadcast
│       │   │   ├── recovery.py        # rebuild from event log
│       │   │   └── commands.py        # QueuedCommand, origins, reply sinks
│       │   ├── auth/
│       │   └── admin/
│       ├── api/
│       │   ├── http/                  # FastAPI routers
│       │   ├── ws/                    # socket endpoint, subscriptions
│       │   └── schemas/               # Pydantic DTOs, ServerMessage union
│       ├── db/
│       │   ├── models/                # SQLAlchemy 2.0
│       │   ├── repositories/
│       │   └── migrations/            # Alembic
│       └── maps/
│           ├── registry.py
│           └── validator.py
├── frontend/                          # Vite + React 19, FSD
├── data/
│   ├── maps/<map-id>/{map.svg,map.json}
│   └── seeds/questions.csv
├── docs/
└── docker-compose.yml                 # postgres + backend + frontend
```

**The layering rule:** `domain/` imports nothing from `services/`, `api/`, or
`db/`. It is plain Python — frozen dataclasses and functions. This is what makes
the entire ruleset testable in milliseconds without a server, a socket, or a
database.

**Backend:** FastAPI · Pydantic v2 · SQLAlchemy 2.0 (async) · Alembic ·
PostgreSQL · `argon2-cffi`. Tooling: `uv` · `ruff` (lint + format) ·
`mypy --strict` on `domain/` and `services/` · `pytest` + `pytest-asyncio` ·
Hypothesis.

**Frontend:** Vite · React 19 · TypeScript · Tailwind v4 · shadcn/ui · Biome ·
TanStack Router (file-based) / Query / Form · Zod at the boundary ·
`steiger` for FSD layer linting (Biome does not enforce layer direction).

**Type contract:** backend Pydantic models are the single source of truth. A
generation step emits TypeScript from the OpenAPI schema (REST) and from JSON
Schema (the `ServerMessage` discriminated union). Output is committed; CI fails
on drift. Hand-writing WebSocket message types on both sides is the most
reliable way to ship a realtime bug.

**Maps are not in the database.** `data/maps/<id>/{map.svg,map.json}` behind
`maps/registry.py`. Adding a map is a two-file drop, no code change and no
migration. `map.json` holds region ids, display names, and the adjacency list.
`validator.py` asserts that adjacency is symmetric, that the graph is connected,
that every SVG path has a matching region and vice versa, and that the graph
contains an independent set of size ≥ 4 — so base placement (§3.4.1) can never
fail at runtime for a registered map.

---

## 5. Domain model

### 5.1 GameState

```python
@dataclass(frozen=True)
class GameState:
    game_id: GameId
    seq: int
    map_id: MapId
    rules: GameRules
    phase: Phase
    round_no: int
    turn_order: tuple[PlayerId, ...]
    players: Mapping[PlayerId, PlayerState]
    territories: Mapping[RegionId, Territory]
    turn: Turn | None
    question_pool: QuestionPool          # immutable, drawn at GameStarted
    winner_id: PlayerId | None


class Phase(StrEnum):
    LOBBY = "lobby"
    EXPANSION = "expansion"
    BATTLE = "battle"
    FINISHED = "finished"
    ABORTED = "aborted"
```

`ABORTED` is a distinct phase rather than an inference from `winner_id is None`,
so that `FINISHED + winner`, `ABORTED + no winner`, and a possible future
`FINISHED + draw` are all explicit.

```python
@dataclass(frozen=True)
class Territory:
    region_id: RegionId
    owner_id: PlayerId | None
    kind: TerritoryKind                  # NORMAL | BASE
    base_owner_id: PlayerId | None
    base_hp: int | None
    acquisition: AcquisitionKind | None   # CLAIMED | CONQUEST | BASE | None
```

`PlayerState` carries `seat`, `display_name`, `score`, `bonus_score`,
`base_region`, `is_eliminated`.

**Connection status is deliberately absent from `GameState`.** Presence is
broadcast on its own unsequenced channel; the domain must not know that sockets
exist (ADR-003).

### 5.2 Deadlines and window identity

```python
@dataclass(frozen=True)
class Deadline:
    id: DeadlineId
    kind: DeadlineKind
    deadline_at: datetime      # absolute, UTC
```

> **One interaction opportunity = one `DeadlineId`.**

`DeadlineId` is not a timer token. It is the identity of an interaction window,
and it is carried by **every** windowed command — `SubmitAnswer`, `PickRegion`,
`SelectAttackTarget`, `ExpireDeadline`. The client receives it with
`QuestionPresented` / `TurnStarted` and echoes it back.

Fencing on the global `state.seq` would be wrong: legitimate events inside a
window advance `seq`, so a strict comparison discards valid timeouts and a loose
one is not fencing at all.

### 5.3 Turn state machine

```
LOBBY ──GameStarted──► EXPANSION
                          │
              ┌───────────┴────────────┐
              ▼                        ▼
      ExpansionQuestion ──────► ExpansionPicking
      (numeric, all answer)     (ranked; one window per pick)
              ▲                        │
              └──── rounds remain ─────┤
                                       │ rounds spent OR map full
                                       ▼
                                     BATTLE
                          ┌────────────┴───────────┐
                          ▼                        │
                 BattleTargetSelect                │
                     │         │                   │
            owned ◄──┘         └──► neutral        │
              ▼                        ▼           │
          BattleDuel            NeutralChallenge   │
              │                        │           │
        both ✓│                        │           │
              ▼                        │           │
        BattleTiebreak ────────────────┴──► next attacker
                                       │ rounds spent
                                       ▼
                                   FINISHED
                        (score tie ──► FinalTiebreak)
```

### 5.4 Commands and the decide/evolve split

```python
events     = decide(state, command, ctx)
new_state  = evolve(state, event)          # folded over events
```

`decide` answers *what happened*; `evolve` answers *what the state becomes*.
Replay is then literally `fold(evolve, events)`, and analytics never has to
reverse-engineer state changes.

Commands: `JoinGame`, `StartGame`, `SubmitAnswer`, `PickRegion`,
`SelectAttackTarget`, `ExpireDeadline`, `Surrender`, `AbortGame`.

**Non-determinism is injected as values, never as capabilities.**

```python
@dataclass(frozen=True)
class DecisionContext:
    now: datetime
    shuffled_player_ids: tuple[PlayerId, ...] | None = None
    shuffled_region_ids: tuple[RegionId, ...] | None = None
    drawn_pool: QuestionPool | None = None      # only for StartGame
```

A `random.Random` instance would be mutable non-determinism smuggled inside a
pure function. A question repository would be a capability, letting I/O leak
into the domain. The runtime materialises concrete values *before* enqueueing;
the domain is then a mathematical function of `(State, Command,
DecisionContext)`. `evolve` takes events only and never needs `ctx`, because the
events record what was chosen.

### 5.5 Event taxonomy

```
lifecycle   GameCreated · PlayerJoined · PlayerLeft · GameStarted
            BasesAssigned · QuestionPoolDrawn · GameFinished · GameAborted
question    QuestionPresented · AnswerSubmitted · AnswerWindowClosed
            QuestionResolved
expansion   ExpansionRoundStarted · PicksGranted · TerritoryClaimed
            ExpansionRoundCompleted
battle      BattleRoundStarted · TurnStarted · TurnSkipped · TurnAborted
            AttackDeclared · DuelResolved · TiebreakStarted
            TerritoryCaptured · DefenseHeld · BaseDamaged · BaseDestroyed
            NeutralTerritoryCaptured · NeutralAttackFailed
scoring     ScoreChanged
terminal    PlayerEliminated · PlayerSurrendered · TerritoryNeutralized
            FinalTiebreakStarted
```

### 5.6 The question pool is immutable and self-contained

An admin can edit or deactivate a question at any moment, including while a live
game holds it. Therefore:

```python
@dataclass(frozen=True)
class QuestionSnapshot:
    question_id: QuestionId
    version: int
    kind: QuestionKind
    prompt: str
    category: QuestionCategorySnapshot
    difficulty: Difficulty
    choices: tuple[ChoiceSnapshot, ...] | None    # MC
    numeric_answer: Decimal | None                # NUMERIC
    unit: str | None
    media_asset_id: MediaAssetId | None
```

`QuestionPoolDrawn` persists the **complete** pool as `QuestionSnapshot`s, not
ids. `QuestionPresented` also carries a full `QuestionSnapshot`. The duplication
is intentional: the log stays small, and each event is independently
understandable.

```
questions table
      │
      ▼
QuestionPoolDrawn        ← the only read from the bank
      │
      ╳  no dependency after this point
      ▼
immutable game question pool
```

Without this, a crash after an admin edits an as-yet-unpresented question would
force recovery to re-read the bank — violating the rule and silently changing
the game.

**Media is referenced by `media_asset_id`, never by URL.** A URL is deployment
state (`/media/x` today, a CDN later); historical game data must not depend on
it. Projection turns the id into a URL at the boundary.

### 5.7 Question budget — one implementation

```python
@dataclass(frozen=True)
class QuestionBudget:
    numeric: int
    multiple_choice: int


def required_question_budget(rules: GameRules) -> QuestionBudget:
    duels = rules.battle_rounds * rules.player_count
    return QuestionBudget(
        numeric=rules.expansion_rounds + duels + 1,   # + final tiebreak
        multiple_choice=duels,
    )
```

Defaults (3 players, 4 + 4 rounds) → 17 numeric, 12 MC.

This is an **upper bound over every possible trajectory**, and it is a single
pure function used by the preset UI, game creation, `StartGame`,
`QuestionPoolDrawn`, and the tests. Four independent formulas would diverge the
first time the rules change.

---

## 6. The reducer

### 6.1 Reject versus ignore

```
ignore  ( · )  decide() returns []
               benign race: stale window, late answer, early timer
               nothing persisted, nothing broadcast, client hears nothing

reject  ( ✗ )  decide() raises RejectedCommand(code)
               client bug or cheat attempt
               nothing persisted, error sent to the sender only
```

The distinction is not cosmetic. A player who clicked 100 ms before the deadline
must not see a red toast; a player attacking a non-adjacent region must.

### 6.2 Guard pipeline

Guards run in this order. The stale-window check deliberately precedes actor
validation: a stale packet from a since-eliminated player is a benign race, not
a violation.

```
1. phase ∈ {FINISHED, ABORTED}              → ·
2. windowed cmd: deadline_id ≠ current      → ·
3. actor is not an active participant       → ✗ NOT_A_PARTICIPANT
4. ExpireDeadline and ctx.now < deadline_at → ·
5. command illegal for this Turn            → ✗ WRONG_TURN_STATE
6. domain constraint violated               → ✗ <specific code>
7. produce events
```

**Idempotency:** a repeated `SubmitAnswer` in the same window with the **same**
value → `·` (double click). With a **different** value → ✗ `ALREADY_ANSWERED`.

### 6.3 Transition matrix

`→` accepted · `·` ignored · `✗` rejected. All 80 cells defined.

| Turn \ Command | JOIN | START | ANSWER | PICK | TARGET | EXPIRE | SURRENDER | ABORT |
|---|---|---|---|---|---|---|---|---|
| `LOBBY` (turn=None) | → | → | ✗ | ✗ | ✗ | · | → *(leave)* | → |
| `EXPANSION/Question` | ✗ | ✗ | → | ✗ | ✗ | → | → | → |
| `EXPANSION/Picking` | ✗ | ✗ | ✗ | → | ✗ | → | → | → |
| `BATTLE/TargetSelect` | ✗ | ✗ | ✗ | ✗ | → | → | → | → |
| `BATTLE/Duel` | ✗ | ✗ | → | ✗ | ✗ | → | → | → |
| `BATTLE/Tiebreak` | ✗ | ✗ | → | ✗ | ✗ | → | → | → |
| `BATTLE/NeutralChallenge` | ✗ | ✗ | → | ✗ | ✗ | → | → | → |
| `FINAL/Tiebreak` | ✗ | ✗ | → | ✗ | ✗ | → | · | → |
| `FINISHED` / `ABORTED` | ✗ | ✗ | · | · | · | · | · | ✗ |

### 6.4 Race cases, resolved

**Last answer races the timeout.** The queue is serialised, so there is no race
— only an order. `ANSWER` first: the window closes normally, window `D18` opens,
and the arriving `ExpireDeadline(D17)` fails guard 2. `EXPIRE` first: the
question resolves with the player marked as not having answered, and the
arriving `SubmitAnswer(D17)` fails guard 2 — **silently**. Dequeue order is
authoritative; the WS-frame-to-dequeue latency is microseconds, so fairness is
not affected in practice. The client additionally stops accepting input at its
locally computed deadline (§8.6).

**Target destroyed before a stale command arrives.** `SelectAttackTarget` carries
its window's `DeadlineId`. A different window fails guard 2. The same window
cannot have had its state changed, because only this runtime mutates it and it
was busy with this command.

**Base destruction eliminates a player with a queued command.**
`PlayerEliminated` removes them from the active set, so guard 3 rejects further
commands as `NOT_A_PARTICIPANT` — while any *stale-window* packet still in
flight is silently dropped by guard 2 first. `turn_order` is filtered by active
players; if the eliminated player was next to attack, the turn moves on. One
active player remaining → `GameFinished`.

---

## 7. Persistence

```
users(id, username UNIQUE, password_hash, display_name, role,
      is_active, created_at)
sessions(id, user_id, token_hash, expires_at, revoked_at)
invite_codes(code PK, created_by, expires_at, used_by, used_at, revoked_at)

categories(id, slug UNIQUE, name)
questions(id, version, kind, prompt, category_id, difficulty,
          media_asset_id, is_active, prompt_hash, created_at, updated_at)
question_choices(question_id, idx, text, is_correct)      -- MC
question_numeric(question_id, correct_value NUMERIC, unit) -- NUMERIC
media_assets(id PK /* = sha256 */, mime_type, width, height,
             byte_size, storage_key, created_by, created_at)

rule_presets(id, name, is_default, rules JSONB, version)
games(id, map_id, rules JSONB /* frozen */, preset_id, status, host_id,
      created_at, started_at, finished_at, winner_id, last_seq)
game_players(game_id, user_id, seat, final_score,
             PK(game_id, user_id), UNIQUE(game_id, seat))
game_events(game_id, seq, operation_id, type, payload JSONB, created_at,
            PK(game_id, seq))
             INDEX(game_id, operation_id)
```

**Sessions are opaque tokens, not JWTs.** Admin user management requires that
deactivating a user logs them out *now*; a stateless JWT cannot do that without
a denylist, which is a session table in disguise. The cookie is `httpOnly`,
`SameSite=Lax`, and rides the WebSocket handshake for free on the same origin.

**Questions are never physically deleted** — only `is_active = false`, because
`game_events` reference them and Spec 2 analytics reads them.

**`questions.version` increments on any semantic edit** (prompt, choices,
correct answer, category, difficulty, media, unit). Toggling `is_active` does
not. Without this, Spec 2 would silently merge the statistics of two materially
different questions that share an id.

**Exactly one default preset** is enforced by a PostgreSQL partial unique index
on `is_default WHERE is_default`. Application logic ensures there is never zero.

**No snapshot table in Spec 1.** A game is a few hundred events;
`fold(evolve, events)` on restart is instant. Add snapshots only if that stops
being true.

---

## 8. Realtime protocol

### 8.1 Transport

One authenticated, multiplexed socket per browser tab.

```
/ws  ── session cookie on handshake
 │
 ├── subscribe "lobby"
 ├── subscribe "game:{id}"
 └── subscribe "admin:games", "admin:game:{id}"   ← Spec 2
```

Multiplexing keeps sequencing, resync, backpressure, heartbeat, and future admin
channels in one transport implementation. Under ADR-002 the streams have no
differing scaling characteristics that would justify separate endpoints.

**Every `subscribe` performs its own authorization.** Socket-level
authentication is not sufficient. In Spec 1 a user may subscribe only to a game
they participate in; otherwise `4403`.

### 8.2 Where commands live

```
REST                              WebSocket
────                              ─────────
auth, invite redeem               SubmitAnswer
question CRUD / import            PickRegion
presets CRUD, user admin          SelectAttackTarget
create game, join, start          Surrender
```

Windowed commands go over WebSocket, where latency matters. Everything else is
REST. **The mechanism is the same:** both paths enqueue a `QueuedCommand` on the
one serialised queue. There is no second route by which state can mutate.

```python
QueuedCommand(
    operation_id=OperationId(...),
    command=...,
    origin=WsOrigin(connection_id, command_id) | RestOrigin(future),
)
```

The WS handler does **not** await a future — leaving an unobserved
`asyncio.Future` behind either produces `Task exception was never retrieved` or
silently swallows the rejection. Instead the dispatcher routes
`RejectedCommand` back through the origin. REST genuinely awaits its result and
maps rejection to HTTP 409 with the code.

### 8.3 Message envelope

Pydantic discriminated union on `type`; JSON Schema and TypeScript are generated
from the same definitions.

```
server → client                     client → server
───────────────                     ───────────────
hello         server_time           subscribe    topic
game.snapshot seq, state            unsubscribe  topic
game.update   base_seq, seq,        resync       topic
              state, events         command      command_id, game_id,
game.presence connected[]                        deadline_id, payload
lobby.snapshot / lobby.update       ping
error         command_id, code
pong
```

`game.presence` is deliberately **not** a domain event: no `seq`, not persisted,
absent from replay.

`command_id` is client-generated and echoed back on errors. It is transport
correlation only — with several actions pending, the frontend otherwise cannot
tell which one a `REGION_NOT_FREE` belongs to. It is **not** used for automatic
retry; the "never resend after reconnect" rule stands, and idempotency remains a
domain-level concern (§6.2).

### 8.4 The transport unit is the command, not the event

Projection may map a domain event to `None`. Per-event sequencing then breaks:
the client sees 101, 103, concludes there is a gap, resyncs, and repeats
forever. So the unit of transport is the whole committed batch produced by one
command:

```
state @ seq 80
   │
SubmitAnswer  →  [AnswerSubmitted 81, AnswerWindowClosed 82,
                  QuestionResolved 83, PicksGranted 84]
   │
COMMIT 81..84
   │
   ▼
game.update { base_seq: 80, seq: 84,
              state:  <full projected snapshot>,
              events: [PlayerAnswered, QuestionResolved, PicksGranted] }
```

Client logic:

```
base_seq == last_seq  → apply, last_seq = seq
seq <= last_seq       → duplicate, ignore
otherwise             → resync
```

This also matches ADR-001/4 exactly: one command decided, one atomic commit, one
externally visible publication.

### 8.5 Reconnect

```
socket dies → exponential backoff 0.5 s → 8 s with jitter
            → /ws handshake (cookie)     → 4401 ⇒ login
            → hello (server_time)
            → subscribe "game:{id}"
            → game.snapshot (seq = N, projected for this viewer)
            → render from scratch; last_seq = N
```

Snapshot on reconnect, never event catch-up: a whole game state is a couple of
kilobytes, and incremental catch-up does not pay for its complexity. The client
never resends commands — it renders the snapshot; if its answer is there it was
accepted, if not, the window has moved on. Timers kept running (ADR-003), which
is visible in the snapshot's `deadline_at`.

### 8.6 Clock, heartbeat, backpressure

`hello` carries `server_time`. The client refines the offset using the existing
ping/pong round trip (`S + (C2 − C1) / 2`) rather than a snapshot timestamp,
which would embed one-way network delay. This is **rendering only**; the
authoritative rule remains `ctx.now >= deadline_at` on the server.

Heartbeat: `ping` every 15 s, socket considered dead after 30 s of silence.
Presence changes broadcast `game.presence`.

The runtime must never await a socket write:

```
runtime ──put_nowait──► bounded outbound queue (~64) ──► sender task ──► socket
                              │ QueueFull
                              ▼
                        close(4408) — client reconnects and takes a snapshot
```

### 8.7 Projection

```
DomainEvent  ──project(event, viewer)──►  ClientEvent | None
GameState    ──project_snapshot(state, viewer)──►  ClientGameState
```

```python
@dataclass(frozen=True)
class ViewerContext:
    user_id: UserId
    player_id: PlayerId | None
    role: UserRole
```

`DomainEvent` and `ServerMessage` are **separate types with no shared base
class**, so that `websocket.send_json(event.model_dump())` cannot compile.

| Domain event | to a participant | to its author |
|---|---|---|
| `QuestionPresented` | prompt, choices, media, `deadline_at`, `deadline_id`; **no** correct answer | same |
| `AnswerSubmitted` | `PlayerAnswered(player_id)` — the fact, not the value | own value visible |
| `QuestionResolved` | full: correct answer, all values, ranking | same |

The pre-resolution DTO does not *contain* the answer fields at all (§12.3), so
serialization cannot leak them by accident.

### 8.8 The projection carries affordances, not just facts

To grey out illegal moves the client would need adjacency and ownership rules —
a fragment of the ruleset. It does not derive them. The server supplies them,
per viewer:

```json
"turn": {
  "kind": "battle_target_select",
  "deadline_id": "...", "deadline_at": "...",
  "actor": "p1",
  "your_options": { "attack": ["R3", "R7", "R12"] }
}
```

The client highlights exactly `your_options`. The server still validates
(guard 6). Adjacency lives in one place: `domain/maps`.

---

## 9. Frontend

### 9.1 State is transported; events narrate

If the client folded `game.events` into state, there would be two game engines —
one in Python, one in TypeScript — and they *would* diverge. So the client never
folds. Each `game.update` carries both, and they go to two disjoint sinks:

```
msg.state  ──► queryClient.setQueryData(["game", id], state)     ← truth
msg.events ──► ephemeral event bus                               ← narration
               toasts, capture animation, sound, battle log
               never writes to the cache
```

A full state per command costs a couple of kilobytes at ~60 commands per match.
In exchange, a whole class of bug disappears, and a gap in `seq` stops being a
correctness problem: apply `msg.state`, discard the events (losing an animation,
not state), set `last_seq`, continue. Resync is then needed only after a
reconnect.

### 9.2 Ownership of state

| Store | Contents | Explicitly not |
|---|---|---|
| Query `["game", id]` | `GameSnapshot`, authored by the server | — |
| Query `["lobby"]`, `["questions", f]`, `["presets"]`, `["me"]` | REST data | — |
| Event bus (not a store) | ephemeral narration | anything outliving a frame |
| Zustand | `selectedRegionId`, `mapZoom`, `mapPan`, `openPanel`, `soundEnabled` | territory owner, score, round, current question, timer |
| TanStack Router | route, `gameId` | — |
| TanStack Form + Zod | admin forms | — |

### 9.3 First paint and the write race

`["game", id]` has a real `queryFn` — `GET /games/{id}` — returning the same
`GameSnapshot` through the same `project_snapshot(state, viewer)`. One
projection, two transports: the page survives a refresh and renders while the
socket is still connecting.

The REST response can therefore land after a newer WS update. There is exactly
one cache writer, and it compares `seq`:

```ts
const writeGame = (id, incoming) =>
  queryClient.setQueryData(gameKey(id), prev =>
    !prev || incoming.seq >= prev.seq ? incoming : prev
  );
```

Query config: `staleTime: Infinity`, `refetchOnWindowFocus: false`,
`refetchOnReconnect: false`.

### 9.4 FSD layers

```
app/       providers, router root, WsProvider, error boundary,
           ws→cache dispatcher, useMediaPrefetch
pages/     lobby · game · admin/{questions,invites,presets}
widgets/   game-stage · question-dock · scoreboard · battle-log · player-strip
features/  create-game · join-game · submit-answer · pick-region ·
           select-target · admin-question-editor · admin-question-import ·
           admin-invite-issue
entities/  game · player · question · territory   (types, selectors, cache keys)
shared/    api/{generated,rest,ws} · ui (shadcn) · lib · config
```

The WS client in `shared/api/ws` is dumb — connect, subscribe, typed messages,
no knowledge of the cache. The dispatcher that routes `game.update` into the
cache and the bus lives in `app/`, which may import `entities`. Otherwise
`shared` would import upward. Components never see the socket:
`useGameSubscription(gameId)` (refcounted) and `useGame(gameId)`.

### 9.5 Game screen layout

A fixed-geometry **stage** shows either the map or the question image; the
**dock** below always holds question text, answers, and the timer.

```
┌─────────────────────────────────────────┐
│  player strip · name · score · ♜♜♜       │
├─────────────────────────────────────────┤
│                                         │
│  STAGE (fixed height)                   │
│    map            — default, target      │
│                     selection, picking   │
│    question image — while a media        │
│                     question is open     │
│                                         │
├─────────────────────────────────────────┤
│  DOCK                                   │
│    turn hint  ·  or question + answers  │
│    timer bar                            │
└─────────────────────────────────────────┘
```

The dock's geometry does not depend on whether the question has an image — only
the stage's content changes. One render mode, no layout shift, and the answers
always sit on an opaque surface whose contrast does not depend on which photo an
admin uploaded. There is no separate modal mode.

The question card skeleton reserves the stage height in advance, so nothing
shifts at the moment the timer starts.

### 9.6 Media and timer fairness

An image that begins loading when the timer starts costs a player on a slow
connection real seconds, and the server will not wait (ADR-003). The whole
match's question pool is therefore drawn at `GameStarted`, and the client
prefetches its media on entering the game:

```
game.snapshot → media_prefetch: string[]   → useMediaPrefetch(urls)
```

URLs are content-addressed and opaque (`/api/media/a3f9c1…`), so prefetching
~29 images leaks neither question text nor answers.

Spec 1 supports **one optional illustration per question**, for both MC and
numeric. Image *answers* (a 2×2 grid of images) are Spec 2.

### 9.7 Screens

```
/login · /redeem            sign in, redeem invite
/                           lobby: open games, create (preset + map)
/games/:id                  pre-start room, then the board
/admin/questions · /:id · /import
/admin/invites
/admin/presets
```

---

## 10. Admin (Spec 1)

### 10.1 Bootstrap

```
uv run triviador admin-create --username … --password …
```

Semantics, precisely — so it is safe in a deployment script:

```
no admins exist                       → create
same username already exists as admin → success, no-op
another admin already exists          → refuse unless --force
```

### 10.2 Question bank

List with server-side pagination and filters: `kind`, category, difficulty,
`is_active`, `has_media`, full-text search on `prompt`.

Editor (TanStack Form + Zod from generated types):

```
common     prompt · category · difficulty · media upload · is_active
MC         exactly 4 choices, exactly 1 correct
NUMERIC    correct_value (decimal) · unit (optional: "km", "year")
```

Four choices is fixed, as in the original; a configurable count buys nothing and
costs variability in the answer grid.

Duplicates: `sha256(normalize(prompt))` is computed on save and on import and
surfaces a warning, not a block — legitimately similar phrasings exist.

### 10.3 Bulk import

Two-phase, with no partial writes:

```
POST /admin/questions/import/dry-run          (.csv | .zip)
  → import_id, upload_sha256, per-row report
  → nothing is written

CONFIRM is enabled only when rejected == 0

POST /admin/questions/import/{import_id}/confirm
  → applies exactly that validated upload, one transaction
```

Binding the confirmation to `import_id` + `upload_sha256` prevents confirming a
subtly different second upload. Requiring zero rejected rows keeps
"all-or-nothing" unambiguous; the admin downloads the rejected rows as CSV,
fixes them, and repeats.

`.zip` = `questions.csv` + `media/`. Plain `.csv` is accepted without images.

```
kind, prompt, category, difficulty,
choice_1..choice_4, correct_index,     ← mc
numeric_answer, unit,                  ← numeric
media_file
```

Media ordering exploits content addressing to make a non-transactional
filesystem safe:

```
validate + normalize media
      ↓
write hash-addressed blobs (idempotent)
      ↓
BEGIN → insert media_assets, questions → COMMIT
```

A failed transaction leaves an unreferenced blob, which `media-gc` removes
safely — far better than committing rows and then discovering the media write
failed.

### 10.4 Media pipeline

```
upload → validate (mime, ≤5 MB, ≤4000 px)
       → re-encode to WebP, max 1280 px, strip EXIF/metadata
       → sha256 → /data/media/<ab>/<sha>.webp
       → media_assets row
```

Re-encoding to raster is also the security control: **SVG is rejected for
question media**, because it executes scripts, and re-encoding destroys any
embedded payload. (The map SVG is different — it lives in the repository and is
not user-uploaded.) Assets are immutable, served with `Cache-Control: immutable`.

`uv run triviador media-gc` deletes an asset only when it is referenced by
neither any question **nor** any persisted `QuestionPoolDrawn` /
`QuestionPresented` snapshot. It is a command, not a UI: rare and destructive.

There is no separate media browser in Spec 1; upload happens inside the question
editor.

### 10.5 Invites and users

| | Operations | Constraints |
|---|---|---|
| Invites | issue N codes, expiry, list with status, revoke | redemption is public `POST /auth/redeem` |
| Users | list, deactivate, grant/revoke admin | cannot deactivate self; cannot demote the last admin |

Deactivation kills sessions **immediately** — precisely why §7 chose opaque
tokens. An open socket for that user closes with `4401`.

Last-admin protection must be transactional (check and update under one
transaction with appropriate locking). A `count_admins() == 1` check followed by
a separate update lets two admins concurrently demote each other.

### 10.6 Presets and bank coverage

CRUD over `GameRules`. Validation on save: `len(claims_by_rank) == player_count`,
all counts ≥ 1, timeouts within bounds, exactly one default.

Coverage, computed from `required_question_budget(rules)`:

```
"Classic"  3 players · exp 4 · battle 4
  numeric  need 17 · bank 34   ✓
  mc       need 12 · bank  9   ✗   cannot start

"Quick"    3 players · exp 2 · battle 2
  numeric  need  9 · bank 34   ✓
  mc       need  6 · bank  9   ✓
```

**The admin indicator is informative, not authoritative.** Between viewing it
and starting a game an admin can deactivate questions. Three checkpoints:

```
preset page              informative
CreateGame               may reject if currently insufficient
StartGame / PoolDrawn    AUTHORITATIVE — same transaction/view from which
                         the immutable pool is selected
```

Insufficient at `StartGame` → `QUESTION_POOL_INSUFFICIENT`, the game stays in
`LOBBY`. There is no partial `GameStarted`.

The preset UI states explicitly that editing a preset does not affect running
games (they hold a frozen copy in `games.rules`).

---

## 11. Error handling

### 11.1 Four classes

| Class | Example | Persisted | Broadcast | Delivered to |
|---|---|---|---|---|
| `ignore` | stale `DeadlineId`, repeated identical answer | no | no | nobody |
| `reject` | `NOT_ADJACENT`, `ALREADY_ANSWERED` | no | no | sender only |
| `transport` | session revoked, queue overflow | no | no | close code |
| `fault` | commit failed, exception in `decide` | — | no | runtime quarantine |

Close codes: `4401` missing/revoked session · `4403` not authorized for topic ·
`4408` outbound queue overflow · `1011` internal fault. The client reacts
differently to each: `4401` → login, `4403` → drop the subscription, `4408` →
reconnect immediately, `1011` → reconnect with backoff.

REST uses one envelope `{code, message, details?}` where `code` is a closed enum
carried to the frontend by the same codegen.

### 11.2 Commit is committed before it is published

```
decide() → events
    ↓
BEGIN
  verify games.last_seq == <state.seq>      ← optimistic guard
  insert events, all with this operation_id
  update games.last_seq
COMMIT
    ↓  ok
evolve(memory) → broadcast
```

### 11.3 An ambiguous commit is not a rollback

A connection can drop *while PostgreSQL is committing*. The client then knows
only that the outcome is unknown — and blindly retrying would replay a batch
that is already durable.

Every `QueuedCommand` therefore carries an `operation_id` (the client's
`command_id` for WS commands, a server-generated UUID for timers, REST, and the
watchdog), stamped on every row of the batch.

```
ambiguous COMMIT
      ↓
fresh connection: does any row exist with operation_id = X?
      ↓
   yes → the whole batch committed (batches are atomic) → continue
   no  → safe to retry
```

Failure policy:

```
known rollback (deadlock, serialization failure)  → bounded retry
ambiguous commit outcome                          → reconcile by operation_id
persistence still unavailable                     → quarantine
exception in decide / evolve                      → quarantine, never retry
```

### 11.4 Quarantine destroys a runtime generation

A runtime owns more than state: a command queue, a deadline task, connection
subscriptions, and pending reply sinks. Quarantine discards all of it.

```
GameManager
    ├── atomically detach runtime R17, mark it closed
    ├── drop/reject queued commands
    ├── cancel its deadline task
    ├── close attached sockets (1011)
    └── remove R17

DB log ── replay ──► R18   (a new generation)
```

Nothing queued against R17 may ever surface inside R18. Runtime generations
carry an internal id and this is asserted in tests: `DeadlineId` already protects
*domain* state from a stale timer, but preventing a zombie task from feeding a
dead queue is a separate, operational concern.

### 11.5 Watchdog

A timer task can die (an exception in its body, a cancelled task). The deadline
then never fires and the game stalls silently. `GameManager` scans live runtimes
every 5 s: if there is a current deadline, `now > deadline_at + 5 s`, and no
`ExpireDeadline` has been processed for it, one is enqueued. Cheap insurance
that removes much of the need for manual intervention.

### 11.6 Reaping

```
LOBBY older than N hours            → AbortGame
LOBBY with no connections           → runtime may be unloaded
EXPANSION / BATTLE / FINAL          → runtime stays resident regardless
                                       of presence
FINISHED / ABORTED                  → runtime may be unloaded immediately
```

Active games are **never** evicted. Unloading one would leave nobody owning its
active `DeadlineId`, so the game would pause until someone reconnected — a
direct contradiction of ADR-003. Evicting live games would require a persistent
scheduler able to wake them at `deadline_at`; that is worker infrastructure
Spec 1 explicitly does not need.

### 11.7 Frontend

An error boundary per route, a socket-status banner, query errors through
TanStack Query. Any client-side desync has exactly one resolution: take a fresh
snapshot.

---

## 12. Testing

The highest-risk defects here are cross-boundary invariants, not endpoints. The
strategy is organised accordingly.

### 12.1 Layer 1 — domain (pure, no I/O, milliseconds)

**The transition matrix becomes an executable artifact.** A table-driven test
over all 80 cells of §6.3 asserts exactly one of `→ / · / ✗` per
`(Turn, Command)` pair. Adding a state turns the test red until the matrix is
extended.

Property tests (Hypothesis):

```
score          ∀ event: score(p) == holdings_value(p) + bonus(p)
score-log      Σ ScoreChanged.delta[p] == score(p)
replay         fold(evolve, events) == incrementally evolved state
purity         decide(s, c, ctx) twice → equal events
deadlines      turn ≠ None ⟺ exactly one current Deadline
               terminal phase ⟹ turn is None
ownership      no region is owned by an eliminated player
budget         questions_consumed <= required_question_budget(rules)
               over every generated trajectory
progress       every trajectory of accepted, state-advancing commands plus
               deadline expirations reaches a terminal phase within a bound
               derived from GameRules and map size
```

The budget property tests the *formula* against generated trajectories, rather
than accidentally testing whichever question bank happens to exist.

The progress property is stated over **accepted progress transitions**, not raw
command count — a client can send unboundedly many ignored commands, so raw
counts cannot be bounded. It is implemented with a decreasing measure
(remaining expansion opportunities + remaining battle turns + finite substate
depth of the current turn) and asserts that no accepted transition creates an
unbounded cycle. A `RuleBasedStateMachine` drives it; this is the single
highest-value test in the suite, because it finds dead ends that hand-written
tests never do.

### 12.2 Layer 2 — runtime + PostgreSQL (asyncio, no HTTP)

```
committed-but-not-broadcast   break the broadcaster after commit
                              → the reconnect snapshot contains the change

broadcast-but-not-committed   break the commit
                              → nothing sent, memory unchanged, quarantined

ambiguous commit              drop the connection during COMMIT
                              → reconciliation by operation_id, no duplicate
                                batch, no lost batch

stale window                  ExpireDeadline(D17) after the window is D18
                              → zero events, zero sends

recovery, active deadline     deadline +20 s, kill runtime, restart at T+8
                                → timer fires at the original absolute time
                              restart at T+25
                                → ExpireDeadline enqueued immediately

pool immutability             draw the pool, rewrite the questions rows,
                              restart → original snapshots are presented

serialization                 N commands from M connections at once
                              → seq contiguous, UNIQUE(game_id, seq) intact

backpressure                  a connection that never reads
                              → loop latency bounded, socket closed 4408,
                                the game continues

generation quarantine         commands queued against R17 never reach R18

presence                      disconnecting the last player does not cancel
                              a deadline task or pause the game

idempotency                   same answer twice → one AnswerSubmitted, no error
                              different answer  → error, still one
```

This imposes design requirements, not just test requirements: **the repository,
the broadcaster, and the clock are protocols**. Tests advance time manually;
`ctx.now` comes from a `Clock`; there is no real-time `asyncio.sleep` in tests.

### 12.3 Layer 3 — HTTP / WS contracts

- **Type drift.** CI regenerates TypeScript and fails if the output differs from
  what is committed. This is the contract test for both REST and the WS envelope.
- **Structural secret redaction.** Byte-scanning is the wrong test: for an MC
  question the correct answer legitimately appears as ordinary choice text, and
  a numeric answer can coincide with a number in the prompt. The test asserts
  *structure* — that before `QuestionResolved` no client-bound payload carries
  `correct_index`, `is_correct`, `correct_choice_id`, or `correct_value`, and
  that after it they are present. The pre-resolution DTO does not declare those
  fields at all, so serialization cannot include them by accident.
- **Topic authorization.** A player subscribing to someone else's game → `4403`.
- **Session revocation.** Deactivating a user closes their open socket with `4401`.
- **Import semantics.** Confirm with a different `upload_sha256` → refused;
  confirm with `rejected > 0` → refused; a DB failure inside confirm → zero
  questions written, blobs orphaned and collectable by `media-gc`.

### 12.4 Layer 4 — one E2E

Exactly one Playwright scenario: three browser contexts, invite redemption,
create → join → start, a full match on a shortened preset (exp 1 / battle 1) to
`FINISHED`. Not a suite — one smoke test proving the seams line up.

### 12.5 What is deliberately not tested

Endpoint-by-endpoint CRUD coverage. Request shape is held by Pydantic and the
codegen; there is little logic there.

### 12.6 CI gates

```
ruff · mypy --strict (domain, services) · biome · steiger
pytest — 100 % branch coverage on domain/game/reducer.py
generated-contract drift check
playwright smoke
```

The coverage floor applies **only** to the reducer, which is pure and small
enough for the number to carry meaning. A global percentage across FastAPI
routers mostly rewards testing plumbing.

---

## 13. Out of scope (Spec 2)

- Match history browsing and per-round replay
- Per-question analytics (answer rates, response times, broken-question detection)
- Live game spectating by non-participants
- Admin live control: watch in-progress games, force-advance, abort
- Image answers (2×2 grid of images instead of text choices)
- Multiple maps and a lobby map picker
- Horizontal scaling (game ownership, message broker, persistent scheduler)

The event log, the `ViewerContext` projection abstraction, and the
`admin:games` topic naming are all in place in Spec 1 so that none of these
requires reworking the engine.

---

## 14. Open items for implementation

1. **Which map ships first.** Any country SVG with ~15 regions from a free source
   (MapSvg, simplemaps, amCharts), plus a hand-authored adjacency list. Licensing
   must be checked and recorded in `data/maps/<id>/LICENSE`.
2. **`steiger` configuration.** The exact FSD rule set to enforce is to be
   settled during implementation.
3. **Seed question bank.** At minimum 17 numeric + 12 MC active questions, or the
   default preset cannot start a game. The seed CSV must satisfy this.
