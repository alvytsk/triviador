# Triviador Plan 2 — Domain Amendments Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Correct six defects and gaps in the pure domain core that the persistence, runtime, and API layers cannot be built on top of as they stand.

**Architecture:** Pure event-sourced domain, unchanged. `decide(state, command, ctx) -> events` answers *what happened*; `evolve(state, event) -> state` answers *what the state becomes*. Every change here stays inside `domain/` (plus one filesystem-side addition to `maps/registry.py`) and performs no I/O.

**Tech Stack:** Python 3.13 · `uv` · frozen dataclasses · `ruff` · `mypy --strict` · `pytest` · `Hypothesis`

**Spec:** `docs/superpowers/specs/2026-08-16-triviador-app-architecture-design.md` §3 (with §2 A-5, A-6, A-8 as the amendments being implemented)

## Global Constraints

Every task's requirements implicitly include this section.

- **ADR-001/3:** the domain layer performs **no I/O**. `domain/` must not import `services/`, `api/`, `db/`, or any library that touches the network, filesystem, clock, or RNG. `hashlib` and `json` are permitted in `domain/maps/digest.py` — they are pure computation over a value already in memory.
- **No hidden non-determinism.** `decide` must never call `random`, `datetime.now()`, or `uuid.uuid4()`. Everything comes from `state`, `command`, or `ctx`. `evolve` takes events only.
- **Everything is frozen.** All domain types are `@dataclass(frozen=True)`; collections in state are `Mapping`/`tuple`/`frozenset`.
- **`ScoreChanged` is a first-class event.** Never embed `score_delta` or `new_score` into gameplay events.
- **One interaction opportunity = one `DeadlineId`.**
- **Guard order is fixed** (Spec 1 §6.2): terminal phase → stale window → actor validity → early expire → turn legality → domain constraint.
- **`ignore` vs `reject`:** benign races return `()`; client bugs raise `RejectedCommand`.
- **`GameRules` is constructed by keyword everywhere.** Adding a field mid-dataclass is safe; verify with `grep -rn "GameRules(" backend/` before assuming otherwise.
- Python `>=3.13`. Line length 100. `ruff check`, `ruff format --check`, and `mypy --strict` must pass on every commit.
- **The reducer carries a 100 % branch coverage gate** (Spec 1 §12.6). Every new branch in `reducer.py` needs a test that reaches it, including `raise` branches.

---

## File Structure

```
backend/src/triviador/domain/
├── game/
│   ├── rules.py         MODIFY  + warmup_ms, MIN/MAX_WARMUP_MS, validation
│   ├── state.py         MODIFY  + DeadlineKind.WARMUP, MediaWarmup turn variant
│   ├── actions.py       MODIFY  AbortGame.actor_id becomes optional
│   ├── events.py        MODIFY  + MediaWarmupStarted, GameCreated.map_sha256
│   ├── reducer.py       MODIFY  seat allocation, surrender endgame check,
│   │                            warmup wiring, GenesisEventNotFoldable arm
│   └── genesis.py       CREATE  create_initial_state, GenesisEventNotFoldable
└── maps/
    └── digest.py        CREATE  canonical_digest — pure, no I/O

backend/src/triviador/maps/
└── registry.py          MODIFY  + LoadedMap, load_with_digest

backend/tests/
├── conftest.py                        MODIFY  DEFAULT_RULES gains warmup_ms indirectly
├── domain/game/conftest.py            MODIFY  media_warmup state, _expansion_question
├── domain/game/test_join.py           CREATE  seat allocation
├── domain/game/test_rules.py          MODIFY  warmup_ms bounds
├── domain/game/test_abort.py          CREATE  system-authorized abort
├── domain/game/test_surrender.py      MODIFY  last-active-player finish
├── domain/game/test_warmup.py         CREATE  MediaWarmup behaviour
├── domain/game/test_start.py          MODIFY  opening sequence ends at warmup
├── domain/game/test_matrix.py         MODIFY  88 cells
├── domain/game/test_genesis.py        CREATE  create_initial_state
├── domain/maps/test_digest.py         CREATE  canonical_digest
└── maps/test_registry.py              MODIFY  load_with_digest
```

`genesis.py` is its own file rather than another function in the 1175-line `reducer.py`: it is the one constructor that runs *before* any reducer logic, it is what recovery calls, and keeping it separate is what lets a reader answer "where does a `GameState` come from?" without reading the reducer.

`digest.py` sits in `domain/maps/` because canonicalisation is a pure function of a parsed value; the *reading* of the file stays in `maps/registry.py`, outside the domain.

---

## Task ordering

Tasks 1, 2, 3, and 6 are independent. Task 4 must land before Task 5, because Task 5's specified surrender behaviour depends on it. Do them in order.

---

### Task 1: Lowest-unused seat allocation

**Files:**
- Modify: `backend/src/triviador/domain/game/reducer.py:155-160` (`_decide_join`)
- Test: `backend/tests/domain/game/test_join.py` (create)

**Interfaces:**
- Consumes: `JoinGame`, `GameState`, `ev.PlayerJoined` — all existing.
- Produces: no new names. `_decide_join` keeps its signature `(state: GameState, command: JoinGame) -> tuple[ev.GameEvent, ...]`.

`reducer.py:160` currently emits `seat=len(state.players)`. `reducer.py:897-901` already documents the consequence: a lobby `PlayerLeft` does not renumber seats, so a later `JoinGame` re-mints a seat a remaining player still holds. Harmless in a pure domain, fatal against `UNIQUE(game_id, seat)` in Plan 3.

- [ ] **Step 1: Write the failing test**

Create `backend/tests/domain/game/test_join.py`:

```python
"""Seat allocation. Seats are an identity, not a counter."""

from tests.conftest import NOW, lobby_state
from triviador.domain.game import events as ev
from triviador.domain.game.actions import DecisionContext, JoinGame, Surrender
from triviador.domain.game.reducer import decide, fold
from triviador.domain.ids import PlayerId

CTX = DecisionContext(now=NOW)


def test_first_join_takes_seat_zero() -> None:
    state = lobby_state(players={})
    assert decide(state, JoinGame(PlayerId("p1"), "One"), CTX) == (
        ev.PlayerJoined(PlayerId("p1"), "One", seat=0),
    )


def test_join_takes_the_next_free_seat() -> None:
    state = lobby_state(players={"p1": 0, "p2": 1})
    assert decide(state, JoinGame(PlayerId("p3"), "Three"), CTX) == (
        ev.PlayerJoined(PlayerId("p3"), "Three", seat=2),
    )


def test_a_seat_freed_from_the_middle_is_reused() -> None:
    """The regression: p2 leaves seat 1, p4 joins and must take seat 1 — not
    seat 2, which p3 still holds. `UNIQUE(game_id, seat)` in Plan 3 makes the
    old `seat=len(players)` behaviour a hard failure."""
    state = lobby_state(players={"p1": 0, "p2": 1, "p3": 2})
    state = fold(state, decide(state, Surrender(PlayerId("p2")), CTX))

    events = decide(state, JoinGame(PlayerId("p4"), "Four"), CTX)

    assert events == (ev.PlayerJoined(PlayerId("p4"), "Four", seat=1),)
    after = fold(state, events)
    seats = sorted(p.seat for p in after.players.values())
    assert seats == [0, 1, 2], "seats must stay unique"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && uv run pytest tests/domain/game/test_join.py -v --no-cov`
Expected: `test_a_seat_freed_from_the_middle_is_reused` FAILS — the emitted seat is 2, colliding with p3.

- [ ] **Step 3: Write the implementation**

In `backend/src/triviador/domain/game/reducer.py`, replace the body of `_decide_join`:

```python
def _decide_join(state: GameState, command: JoinGame) -> tuple[ev.GameEvent, ...]:
    if command.actor_id in state.players:
        raise RejectedCommand(RejectCode.ALREADY_JOINED, f"{command.actor_id!r} already joined")
    if len(state.players) >= state.rules.player_count:
        raise RejectedCommand(RejectCode.GAME_FULL, "lobby is full")
    # Lowest unused seat, not a counter: a lobby departure frees its seat, and
    # `seat=len(players)` would re-mint a number a remaining player still holds.
    # The full-lobby guard above means the range is never exhausted.
    used = {p.seat for p in state.players.values()}
    seat = min(i for i in range(state.rules.player_count) if i not in used)
    return (ev.PlayerJoined(command.actor_id, command.display_name, seat=seat),)
```

- [ ] **Step 4: Fix the stale comment**

In the same file, in the `case ev.PlayerLeft(player_id=pid):` arm of `_apply` (around line 892), replace the note that begins `NOTE: seats are not renumbered, so` and ends `for this arm.` with:

```python
            # Seats are deliberately not renumbered: a seat is an identity, not
            # a position. `_decide_join` allocates the lowest unused seat, so a
            # departure frees exactly that number for the next joiner.
```

- [ ] **Step 5: Run the full suite and the linters**

Run: `cd backend && uv run pytest -q && uv run ruff check . && uv run ruff format --check . && uv run mypy`
Expected: all PASS. `tests/domain/game/test_start.py::test_joining_an_empty_lobby_emits_player_joined` still passes — an empty lobby's lowest unused seat is 0.

- [ ] **Step 6: Commit**

```bash
git add backend/src/triviador/domain/game/reducer.py backend/tests/domain/game/test_join.py
git commit -m "fix(domain): allocate the lowest unused seat on join"
```

---

### Task 2: System-authorized abort

**Files:**
- Modify: `backend/src/triviador/domain/game/actions.py:53-55` (`AbortGame`)
- Modify: `backend/src/triviador/domain/game/reducer.py:754-755` (`_decide_abort`)
- Test: `backend/tests/domain/game/test_abort.py` (create)

**Interfaces:**
- Produces: `AbortGame(actor_id: PlayerId | None = None)`. A `None` actor means the abort came from the system (Plan 4's reaper), not a player.

Spec §3.3: guard 3 rejects a command whose actor is not an active participant, so the reaper cannot abort an **empty** lobby with an actor-issued `AbortGame`. Making `actor_id` optional is enough, because guard 3 already reads `getattr(command, "actor_id", None)` and skips validation when it is `None` (`reducer.py:87-88`). Crucially this keeps `Command` at eight members, so the §6.3 matrix stays eight columns wide.

- [ ] **Step 1: Write the failing test**

Create `backend/tests/domain/game/test_abort.py`:

```python
"""AbortGame, player-issued and system-issued."""

import pytest

from tests.conftest import NOW, lobby_state
from triviador.domain.game import events as ev
from triviador.domain.game.actions import (
    AbortGame,
    DecisionContext,
    RejectCode,
    RejectedCommand,
)
from triviador.domain.game.reducer import decide, fold
from triviador.domain.game.state import Phase
from triviador.domain.ids import PlayerId

CTX = DecisionContext(now=NOW)


def test_a_player_can_abort_their_own_game() -> None:
    state = lobby_state()
    events = decide(state, AbortGame(PlayerId("p1")), CTX)
    assert events == (ev.GameAborted("aborted by p1"),)
    assert fold(state, events).phase is Phase.ABORTED


def test_a_non_participant_cannot_abort() -> None:
    state = lobby_state()
    with pytest.raises(RejectedCommand) as exc:
        decide(state, AbortGame(PlayerId("stranger")), CTX)
    assert exc.value.code is RejectCode.NOT_A_PARTICIPANT


def test_the_system_can_abort_an_empty_lobby() -> None:
    """The reaper's case: an abandoned lobby has no participants at all, so an
    actor-issued abort can never clear it — guard 3 would reject every possible
    actor."""
    state = lobby_state(players={})
    events = decide(state, AbortGame(), CTX)
    assert events == (ev.GameAborted("aborted by system"),)
    assert fold(state, events).phase is Phase.ABORTED


def test_the_system_can_abort_a_populated_lobby() -> None:
    state = lobby_state()
    assert decide(state, AbortGame(), CTX) == (ev.GameAborted("aborted by system"),)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && uv run pytest tests/domain/game/test_abort.py -v --no-cov`
Expected: the two system tests FAIL with `TypeError: AbortGame.__init__() missing 1 required positional argument: 'actor_id'`.

- [ ] **Step 3: Make the actor optional**

In `backend/src/triviador/domain/game/actions.py`, replace the `AbortGame` definition:

```python
@dataclass(frozen=True)
class AbortGame:
    """`actor_id is None` means a system-issued abort.

    Guard 3 validates the actor only when one is present, so a system abort is
    legal even in a lobby with no participants — which is exactly the reaper's
    case (an abandoned, empty lobby has no actor that could pass guard 3).
    """

    actor_id: PlayerId | None = None
```

- [ ] **Step 4: Name the system in the reason**

In `backend/src/triviador/domain/game/reducer.py`, replace `_decide_abort`:

```python
def _decide_abort(state: GameState, command: AbortGame) -> tuple[ev.GameEvent, ...]:
    who = "system" if command.actor_id is None else command.actor_id
    return (ev.GameAborted(f"aborted by {who}"),)
```

- [ ] **Step 5: Run the full suite and the linters**

Run: `cd backend && uv run pytest -q && uv run ruff check . && uv run ruff format --check . && uv run mypy`
Expected: all PASS. `test_matrix.py` is unaffected — `Command` still has eight members, and the matrix builds `AbortGame(_actor(s))` which still type-checks.

- [ ] **Step 6: Commit**

```bash
git add backend/src/triviador/domain/game/actions.py backend/src/triviador/domain/game/reducer.py backend/tests/domain/game/test_abort.py
git commit -m "feat(domain): allow a system-authorized AbortGame with no actor"
```

---

### Task 3: `warmup_ms` in `GameRules`

**Files:**
- Modify: `backend/src/triviador/domain/game/rules.py`
- Test: `backend/tests/domain/game/test_rules.py`

**Interfaces:**
- Produces: `GameRules.warmup_ms: int`, `MIN_WARMUP_MS = 1_000`, `MAX_WARMUP_MS = 60_000`, `DEFAULT_RULES.warmup_ms == 5_000`.
- Consumed by Task 5, which uses `state.rules.warmup_ms` to size the warmup deadline.

`required_question_budget` is deliberately **unchanged** — a warmup window consumes no question.

- [ ] **Step 1: Write the failing test**

Append to `backend/tests/domain/game/test_rules.py`:

```python
def test_default_warmup_is_five_seconds() -> None:
    assert DEFAULT_RULES.warmup_ms == 5_000


def test_warmup_bounds_are_enforced() -> None:
    assert any("warmup_ms" in p for p in validate_rules(replace(DEFAULT_RULES, warmup_ms=999)))
    assert any("warmup_ms" in p for p in validate_rules(replace(DEFAULT_RULES, warmup_ms=60_001)))
    assert validate_rules(replace(DEFAULT_RULES, warmup_ms=1_000)) == ()
    assert validate_rules(replace(DEFAULT_RULES, warmup_ms=60_000)) == ()


def test_warmup_does_not_change_the_question_budget() -> None:
    """A warmup window presents no question, so it must not move the budget —
    otherwise every preset's coverage check shifts for no reason."""
    baseline = required_question_budget(DEFAULT_RULES)
    assert required_question_budget(replace(DEFAULT_RULES, warmup_ms=30_000)) == baseline
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && uv run pytest tests/domain/game/test_rules.py -v --no-cov`
Expected: FAIL with `TypeError: GameRules.__init__() got an unexpected keyword argument 'warmup_ms'`.

- [ ] **Step 3: Add the field, the bounds, and the validation**

In `backend/src/triviador/domain/game/rules.py`:

Add next to the existing bound constants:

```python
MIN_WARMUP_MS = 1_000
MAX_WARMUP_MS = 60_000
```

Add the field to `GameRules`, immediately after `pick_timeout_ms`:

```python
    pick_timeout_ms: int
    # Fixed window after the pool is drawn, during which the client prefetches
    # every question image before any answer timer starts. Never derived from
    # client readiness — ADR-003 forbids a rule depending on presence.
    warmup_ms: int
```

Add to `DEFAULT_RULES`, immediately after `pick_timeout_ms=15_000,`:

```python
    warmup_ms=5_000,
```

In `validate_rules`, extend the timeout loop to cover it — replace that whole loop with:

```python
    for name, value, low, high in (
        ("answer_timeout_ms", rules.answer_timeout_ms, MIN_TIMEOUT_MS, MAX_TIMEOUT_MS),
        ("pick_timeout_ms", rules.pick_timeout_ms, MIN_TIMEOUT_MS, MAX_TIMEOUT_MS),
        ("warmup_ms", rules.warmup_ms, MIN_WARMUP_MS, MAX_WARMUP_MS),
    ):
        if not low <= value <= high:
            problems.append(f"{name} must be {low}..{high}")
```

- [ ] **Step 4: Run tests and linters**

Run: `cd backend && uv run pytest -q && uv run ruff check . && uv run ruff format --check . && uv run mypy`
Expected: all PASS. Nothing constructs `GameRules` positionally — confirm with `grep -rn "GameRules(" backend/src backend/tests`; every hit should use keywords or `replace`.

- [ ] **Step 5: Commit**

```bash
git add backend/src/triviador/domain/game/rules.py backend/tests/domain/game/test_rules.py
git commit -m "feat(domain): add warmup_ms to GameRules"
```

---

### Task 4: Two surrender defects in non-battle turns

**Files:**
- Modify: `backend/src/triviador/domain/game/reducer.py` (`_decide_surrender`, `_decide_expansion_answer`)
- Test: `backend/tests/domain/game/test_surrender.py`

**Interfaces:**
- Consumes: `_finish(state, ctx) -> tuple[ev.GameEvent, ...]` (existing, `reducer.py:817`).
- Produces: no new names.

**Why this is in Plan 2.** Spec §3.4 requires that a surrender during `MediaWarmup` finishes the game if it leaves one active player. `_decide_surrender` only reaches an endgame check when the surrendering player was *involved in the turn* (`reducer.py:748-750`), and `_is_involved_in_turn` returns `False` for every non-battle turn (`reducer.py:766-767`, the `case _` arm) — including `ExpansionQuestion`, `ExpansionPicking`, and the `MediaWarmup` added in Task 5.

Both defects below are **pre-existing**, not introduced by `MediaWarmup`. Task 5 cannot meet its specification without the first, and the second is the same `active_players()` blind spot one line further on, so they are fixed together, once, for every turn shape.

**Defect A — no endgame check on the uninvolved path.** In a 2-player game, a surrender during expansion leaves one active player and the game keeps running, contradicting Spec 1 §3.6 ("the game finishes when … only one active player remains").

**Defect B — a surrendered player's answer still counts toward closing the window.** `reducer.py:250` compares `len(after.turn.answers)` — every answer ever recorded in this window, including one from a player who has since surrendered — against `len(after.active_players())`, which has shrunk. Spec 1 §3.3 requires every active player to answer or time out.

```
3 players. P1 answers.  answers={P1}          active={P1,P2,P3}   1 < 3  → window stays open
           P1 surrenders.                     active={P2,P3}
           P2 answers.  answers={P1,P2}       active={P2,P3}      2 < 2  → window CLOSES
```

P3 never got to answer, and never timed out. `_rank_numeric` is already correct — it ranks `state.active_players()`, so P1 is excluded from grants — which is exactly why this stayed invisible: the ranking looks right, only the timing is wrong.

- [ ] **Step 1: Write the failing test**

Append to `backend/tests/domain/game/test_surrender.py`:

```python
def test_surrender_during_expansion_finishes_a_two_player_game() -> None:
    """Spec 1 §3.6: one active player remaining ends the game. The surrendering
    player was not "involved in the turn" (an expansion question involves
    everyone equally), which used to mean no endgame check ran at all."""
    from dataclasses import replace as dc_replace

    from tests.conftest import full_pool
    from tests.domain.game.test_start import start_ctx
    from triviador.domain.game.rules import DEFAULT_RULES
    from triviador.domain.game.state import Phase

    two = dc_replace(DEFAULT_RULES, player_count=2, claims_by_rank=(2, 1))
    state = lobby_state(players={"p1": 0, "p2": 1}, rules=two)
    ctx = dc_replace(
        start_ctx(),
        shuffled_player_ids=(PlayerId("p1"), PlayerId("p2")),
        base_regions=(RegionId("r0"), RegionId("r2")),
        drawn_pool=full_pool(),
    )
    state = fold(state, decide(state, StartGame(PlayerId("p1")), ctx))

    events = decide(state, Surrender(PlayerId("p2")), ctx)

    assert any(isinstance(e, ev.GameFinished) for e in events)
    after = fold(state, events)
    assert after.phase is Phase.FINISHED
    assert after.winner_id == PlayerId("p1")


def test_a_surrendered_players_answer_does_not_close_the_window() -> None:
    """Spec 1 §3.3: every *active* player answers or times out. The window used
    to close as soon as `len(answers) >= len(active_players())`, and a
    surrendered player's answer stays in `answers` while they leave `active` —
    so two counts that should both shrink moved toward each other instead."""
    from decimal import Decimal

    from tests.domain.game.test_start import start_ctx
    from triviador.domain.game.state import ExpansionQuestion, NumericAnswer

    state = fold(lobby_state(), decide(lobby_state(), StartGame(PlayerId("p1")), start_ctx()))
    assert isinstance(state.turn, ExpansionQuestion)
    window = state.turn.deadline.id

    def answer(s, who: str, guess: int):
        cmd = SubmitAnswer(PlayerId(who), window, NumericAnswer(Decimal(guess)), 100)
        return fold(s, decide(s, cmd, CTX))

    state = answer(state, "p1", 100)
    state = fold(state, decide(state, Surrender(PlayerId("p1")), CTX))
    state = answer(state, "p2", 110)

    assert isinstance(state.turn, ExpansionQuestion), (
        "p3 has neither answered nor timed out — the window must still be open"
    )
```

Add whatever of `StartGame`, `SubmitAnswer`, `PlayerId`, `RegionId`, `ev`, `fold`, `decide`, `lobby_state`, `CTX` the file does not already import or define — check the existing import block at the top of `test_surrender.py` first and extend it rather than duplicating. Annotate the local `answer` helper's parameter and return as `GameState`.

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && uv run pytest tests/domain/game/test_surrender.py -v --no-cov`
Expected: both new tests FAIL — the first because no `GameFinished` is emitted and `after.phase` is still `Phase.EXPANSION`; the second because the turn has already advanced past `ExpansionQuestion`.

Note: at this point in the plan `StartGame` still opens an `ExpansionQuestion` directly. Task 5 changes it to open a `MediaWarmup`, which is why Task 5 Step 11b renames the first test and Step 11c re-routes the second through the warmup expiry. Their assertions do not change.

- [ ] **Step 3: Add the endgame check**

In `backend/src/triviador/domain/game/reducer.py`, replace the tail of `_decide_surrender` (the block from `if _is_involved_in_turn(...)` to the final `return`):

```python
    if _is_involved_in_turn(state.turn, command.actor_id):
        out.emit(ev.TurnAborted(f"{command.actor_id} surrendered"))
        return (*out.events, *_next_battle_turn(out.state, ctx))

    # Not involved in the open turn — but the elimination may still have left a
    # single player standing (Spec 1 §3.6). `_next_battle_turn` performs this
    # check on the involved path; the uninvolved path had none, so a two-player
    # game silently continued with one active player through every non-battle
    # turn shape.
    if len(out.state.active_players()) <= 1:
        out.emit(ev.TurnAborted(f"{command.actor_id} surrendered"))
        return (*out.events, *_finish(out.state, ctx))
    return tuple(out.events)
```

`TurnAborted` is emitted first so the open window is closed before the game finishes; its `_apply` arm sets `turn=None`, which keeps the "terminal phase ⟹ turn is None" property in `test_properties.py` true.

- [ ] **Step 3b: Count only active players' answers**

In the same file, in `_decide_expansion_answer`, replace the completion check (`reducer.py:250`):

```python
    active = set(after.active_players())
    if len(active & set(after.turn.answers)) < len(active):
        return (recorded,)
```

Intersecting rather than comparing raw lengths is the whole fix: an answer from a player who has since surrendered is no longer counted on either side, so the window closes exactly when every player still in the game has answered.

`_close_expansion_question` and `_rank_numeric` need no change — the ranking already iterates `state.active_players()`.

- [ ] **Step 4: Run the full suite and the linters**

Run: `cd backend && uv run pytest -q && uv run ruff check . && uv run ruff format --check . && uv run mypy`
Expected: all PASS.

If `test_matrix.py::test_cell[surrender-...]` fails for a three-player row, that is a genuine signal to read, not to suppress: with three players a single surrender leaves two active, so the new branch must not fire. Check that the condition is `<= 1` and not `<= 2`.

- [ ] **Step 5: Commit**

```bash
git add backend/src/triviador/domain/game/reducer.py backend/tests/domain/game/test_surrender.py
git commit -m "fix(domain): finish the game when a surrender leaves one active player"
```

---

### Task 5: The `MediaWarmup` turn

**Files:**
- Modify: `backend/src/triviador/domain/game/state.py` (`DeadlineKind`, `MediaWarmup`, `Turn`)
- Modify: `backend/src/triviador/domain/game/events.py` (`MediaWarmupStarted`, `GameEvent`)
- Modify: `backend/src/triviador/domain/game/reducer.py` (`LEGAL_COMMANDS`, `_dispatch`, `_decide_start`, `_close_media_warmup`, `_apply`)
- Modify: `backend/tests/domain/game/conftest.py` (`_expansion_question`, `states`)
- Modify: `backend/tests/domain/game/test_start.py` (`test_start_emits_the_full_opening_sequence`)
- Modify: `backend/tests/domain/game/test_matrix.py` (88 cells)
- Test: `backend/tests/domain/game/test_warmup.py` (create)

**Interfaces:**
- Consumes: `GameRules.warmup_ms` (Task 3), `_finish` behaviour via surrender (Task 4).
- Produces: `DeadlineKind.WARMUP`; `MediaWarmup(deadline: Deadline)` as a `Turn` member; `ev.MediaWarmupStarted(deadline: Deadline)`; `_close_media_warmup(state: GameState, ctx: DecisionContext) -> tuple[ev.GameEvent, ...]`.

Spec §2 A-5: `QuestionPoolDrawn` and the first `QuestionPresented` currently commit in the same batch, so the prefetch list and a live answer deadline reach the client on the same frame — which makes Spec 1 §9.6's fairness argument vacuous. The warmup window is the fix, and it must be a **persisted turn with an absolute deadline**, not a client-side delay, because only a persisted deadline survives a restart (ADR-001/5) and only a fixed duration keeps rules independent of presence (ADR-003).

- [ ] **Step 1: Write the failing test**

Create `backend/tests/domain/game/test_warmup.py`:

```python
"""The media warmup window between the pool draw and the first question."""

from datetime import timedelta

from tests.conftest import NOW, lobby_state
from tests.domain.game.test_start import P1, P2, start_ctx
from triviador.domain.game import events as ev
from triviador.domain.game.actions import (
    AbortGame,
    DecisionContext,
    ExpireDeadline,
    StartGame,
    Surrender,
)
from triviador.domain.game.reducer import decide, fold
from triviador.domain.game.state import (
    DeadlineKind,
    ExpansionQuestion,
    GameState,
    MediaWarmup,
    Phase,
)
from triviador.domain.ids import DeadlineId

LATE = DecisionContext(now=NOW + timedelta(minutes=1))


def started() -> GameState:
    base = lobby_state()
    return fold(base, decide(base, StartGame(P1), start_ctx()))


def test_start_opens_a_warmup_window_not_a_question() -> None:
    state = started()
    assert isinstance(state.turn, MediaWarmup)
    assert state.phase is Phase.EXPANSION
    assert state.turn.deadline.kind is DeadlineKind.WARMUP


def test_the_warmup_deadline_is_warmup_ms_after_now() -> None:
    state = started()
    assert isinstance(state.turn, MediaWarmup)
    assert state.turn.deadline.deadline_at == NOW + timedelta(
        milliseconds=state.rules.warmup_ms
    )


def test_no_question_is_presented_during_warmup() -> None:
    """The whole point: the pool is drawn and prefetchable, but no answer
    timer is running yet."""
    events = decide(lobby_state(), StartGame(P1), start_ctx())
    assert any(isinstance(e, ev.QuestionPoolDrawn) for e in events)
    assert not any(isinstance(e, ev.QuestionPresented) for e in events)


def window(state: GameState) -> DeadlineId:
    deadline = state.current_deadline()
    assert deadline is not None
    return deadline.id


def test_expiring_the_warmup_starts_round_one_and_presents_a_question() -> None:
    state = started()
    events = decide(state, ExpireDeadline(window(state)), LATE)
    kinds = [type(e) for e in events]
    assert kinds == [ev.ExpansionRoundStarted, ev.QuestionPresented]

    after = fold(state, events)
    assert isinstance(after.turn, ExpansionQuestion)
    assert after.round_no == 1
    assert after.turn.deadline.kind is DeadlineKind.ANSWER


def test_an_early_warmup_expiry_is_ignored() -> None:
    """Guard 4: the timer fired before its own deadline."""
    state = started()
    assert decide(state, ExpireDeadline(window(state)), DecisionContext(now=NOW)) == ()


def test_a_stale_warmup_expiry_is_ignored() -> None:
    state = started()
    assert decide(state, ExpireDeadline(DeadlineId(999)), LATE) == ()


def test_the_system_can_abort_during_warmup() -> None:
    state = started()
    after = fold(state, decide(state, AbortGame(), LATE))
    assert after.phase is Phase.ABORTED


def test_surrender_during_warmup_eliminates_without_ending_a_three_player_game() -> None:
    state = started()
    after = fold(state, decide(state, Surrender(P2), LATE))
    assert after.players[P2].is_eliminated
    assert after.phase is Phase.EXPANSION
    assert isinstance(after.turn, MediaWarmup), "the warmup window keeps running"
```

`mypy --strict` needs the `isinstance(state.turn, MediaWarmup)` narrowing before any `state.turn.deadline` access, which is why those assertions appear even where they look redundant. `window()` narrows via `current_deadline()` instead, which is why the tests that use it need no `isinstance`.

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && uv run pytest tests/domain/game/test_warmup.py -v --no-cov`
Expected: FAIL at import — `ImportError: cannot import name 'MediaWarmup' from 'triviador.domain.game.state'`.

- [ ] **Step 3: Add the deadline kind and the turn variant**

In `backend/src/triviador/domain/game/state.py`, extend `DeadlineKind`:

```python
class DeadlineKind(StrEnum):
    ANSWER = "answer"
    PICK = "pick"
    TARGET_SELECT = "target_select"
    WARMUP = "warmup"
```

Add the turn variant immediately before `ExpansionQuestion`:

```python
@dataclass(frozen=True)
class MediaWarmup:
    """The window between the pool being drawn and the first question opening.

    It exists so the client can prefetch every question image before any answer
    timer starts (Spec 1 §9.6). Fixed duration, never client acknowledgement:
    ADR-003 forbids a rule that depends on presence.
    """

    deadline: Deadline
```

Add it to the `Turn` union as the first member:

```python
Turn = (
    MediaWarmup
    | ExpansionQuestion
    | ExpansionPicking
    | BattleTargetSelect
    | BattleDuel
    | BattleTiebreak
    | NeutralChallenge
    | FinalTiebreak
)
```

- [ ] **Step 4: Add the event**

In `backend/src/triviador/domain/game/events.py`, add after `QuestionPoolDrawn`:

```python
@dataclass(frozen=True)
class MediaWarmupStarted:
    deadline: Deadline
```

Add `| MediaWarmupStarted` to the `GameEvent` union, immediately after `QuestionPoolDrawn`.

- [ ] **Step 5: Run to confirm the matrix test now fails loudly**

Run: `cd backend && uv run pytest tests/domain/game/test_matrix.py::test_the_matrix_is_complete -v --no-cov`
Expected: FAIL with `Turn union and TURN_ROWS disagree: missing from TURN_ROWS={<class '...MediaWarmup'>}`.

This is the guard working as designed — `test_matrix.py` cross-references the live `Turn` union, so a new variant cannot be added without extending the matrix.

- [ ] **Step 6: Wire the reducer**

In `backend/src/triviador/domain/game/reducer.py`:

Import `MediaWarmup` from `triviador.domain.game.state` (add it to the existing import block, alphabetically between `GameState` and `NeutralChallenge`).

Add the `LEGAL_COMMANDS` entry, immediately after the `None:` entry:

```python
    MediaWarmup: frozenset({ExpireDeadline, Surrender, AbortGame}),
```

Add the `_dispatch` arm immediately before the `SubmitAnswer() if isinstance(state.turn, ExpansionQuestion)` arm:

```python
        case ExpireDeadline() if isinstance(state.turn, MediaWarmup):
            return _close_media_warmup(state, ctx)
```

Replace the tail of `_decide_start` — everything from the `# Fold what we have` comment to its `return` — with:

```python
    # Fold what we have so the warmup window is opened against real state.
    seeded = fold(state, events)
    deadline, _ = seeded.allocate_deadline(
        DeadlineKind.WARMUP, ctx.now + timedelta(milliseconds=seeded.rules.warmup_ms)
    )
    events.append(ev.MediaWarmupStarted(deadline))
    return tuple(events)
```

Add the new handler immediately after `_decide_start`:

```python
def _close_media_warmup(state: GameState, ctx: DecisionContext) -> tuple[ev.GameEvent, ...]:
    """Warmup expired: open round one. The pool was already drawn at start, so
    nothing is read here — this only starts the first answer window."""
    started = ev.ExpansionRoundStarted(1)
    seeded = evolve(state, started)
    question_events, _ = _open_expansion_question(seeded, ctx)
    return (started, *question_events)
```

Add the `_apply` arm immediately after the `case ev.QuestionPoolDrawn(pool=pool):` arm:

```python
        case ev.MediaWarmupStarted(deadline=deadline):
            return replace(
                state,
                turn=MediaWarmup(deadline),
                next_deadline_id=max(state.next_deadline_id, deadline.id + 1),
            )
```

The `next_deadline_id` bump mirrors `_present_question`: `allocate_deadline` returns a state the caller discards, so `_apply` is what must advance the counter, or the next window would reuse the id.

`ExpansionRoundStarted`'s existing `_apply` arm already sets `turn=None`, so the warmup turn is cleared as round one opens — no extra work.

- [ ] **Step 7: Run the warmup tests**

Run: `cd backend && uv run pytest tests/domain/game/test_warmup.py -v --no-cov`
Expected: all PASS.

- [ ] **Step 8: Update the matrix to 88 cells**

In `backend/tests/domain/game/test_matrix.py`:

Update the module docstring's first line to `"""Spec §6.3 as an executable artifact. 11 turn states x 8 commands = 88 cells.`

Add `MediaWarmup` to the imports from `triviador.domain.game.state`.

Add a `media_warmup` row to `MATRIX`, immediately before `"expansion_question"`:

```python
    "media_warmup": {
        "join": REJECT,
        "start": REJECT,
        "answer": REJECT,
        "pick": REJECT,
        "target": REJECT,
        "expire": ACCEPT,
        "surrender": ACCEPT,
        "abort": ACCEPT,
    },
```

Add the row label to `TURN_ROWS`, immediately after `None: "lobby",`:

```python
    MediaWarmup: "media_warmup",
```

Update the three counts in `test_the_matrix_is_complete`:

```python
    assert len(MATRIX) == 11
    assert all(len(row) == 8 for row in MATRIX.values())
    assert sum(len(row) for row in MATRIX.values()) == 88
```

- [ ] **Step 9: Add the matrix fixture and fix the expansion-question fixture**

In `backend/tests/domain/game/conftest.py`:

Add `ExpireDeadline` to the imports from `triviador.domain.game.actions` if not already present (it is), and add `from datetime import timedelta` at the top.

Add a module-level context that is past every warmup deadline:

```python
# Past any warmup window, so the fixtures below can step through it. Distinct
# from States.ctx, which is NOW and would be an early-expire (guard 4).
_AFTER_WARMUP = DecisionContext(now=NOW + timedelta(minutes=1))
```

Add the warmup state builder and rewrite `_expansion_question`:

```python
def _media_warmup() -> GameState:
    base = lobby_state()
    return fold(base, decide(base, StartGame(P1), start_ctx()))


def _expansion_question() -> GameState:
    """StartGame now opens a warmup window; the first question is one expiry
    later."""
    warmup = _media_warmup()
    assert warmup.turn is not None
    return fold(
        warmup, decide(warmup, ExpireDeadline(warmup.turn.deadline.id), _AFTER_WARMUP)
    )
```

Register the state in the `states` fixture, immediately after `out["lobby"]`:

```python
    out["media_warmup"] = _media_warmup()
```

- [ ] **Step 10: Fix the opening-sequence assertion**

In `backend/tests/domain/game/test_start.py`, `test_start_emits_the_full_opening_sequence` currently asserts the event kinds end with `ev.ExpansionRoundStarted, ev.QuestionPresented`. Replace those two entries with `ev.MediaWarmupStarted`, so the list reads:

```python
    assert kinds == [
        ev.GameStarted,
        ev.BasesAssigned,
        ev.ScoreChanged,
        ev.ScoreChanged,
        ev.ScoreChanged,
        ev.QuestionPoolDrawn,
        ev.MediaWarmupStarted,
    ]
```

Read the rest of that test and the assertions after this block: any that reach into the resulting state expecting an `ExpansionQuestion` turn must now either step through the warmup (as `_expansion_question` does) or assert `MediaWarmup`. Import `ExpansionQuestion` may become unused — remove it if `ruff` flags it.

- [ ] **Step 10b: Fix the round number after start**

`test_after_start_bases_are_owned_and_scored` (`test_start.py:130`) asserts `state.round_no == 1`. Round one now begins when the warmup expires, not when the game starts, so replace that line with:

```python
    assert state.round_no == 0, "round one begins when the warmup expires"
```

Every other assertion in that test — phase, base ownership, base hp, score, `base_region` — is unaffected: `BasesAssigned` and the base `ScoreChanged` events still fire at start.

- [ ] **Step 10c: Route `picking_state()` through the warmup**

`picking_state()` (`test_expansion_picking.py:35`) folds `StartGame` and immediately asserts `isinstance(state.turn, ExpansionQuestion)` in its answer loop, so it breaks at the first iteration. Replace its `state = fold(...)` line with:

```python
    state = fold(base, decide(base, StartGame(P1), start_ctx()))
    assert state.turn is not None
    state = fold(
        state,
        decide(
            state,
            ExpireDeadline(state.turn.deadline.id),
            DecisionContext(now=NOW + timedelta(minutes=1)),
        ),
    )
```

Add `ExpireDeadline` to the imports from `triviador.domain.game.actions` and `timedelta` from `datetime` if the file does not have them.

Note the knock-on: the expansion question's own deadline is now computed from `NOW + 1 minute`, not `NOW`. `CTX` in that file is `DecisionContext(now=NOW)`, which is *before* the new deadline — fine for `SubmitAnswer` (no clock check) but it makes any `ExpireDeadline` in that file an early-expire that guard 4 silently ignores. If a test there expires a pick window, give it a context later than the pick deadline.

- [ ] **Step 11: Run the full suite and fix the remaining fallout**

Run: `cd backend && uv run pytest -q`

Steps 9, 10, 10b, and 10c already cover the failures that could be identified by reading the code. The remainder, all with the same cause — `StartGame` no longer opens a question directly:

- `tests/domain/game/test_expansion_question.py` — any helper that folds `StartGame` and expects an `ExpansionQuestion`. Route it through one `ExpireDeadline` on the warmup window, using a context past the warmup deadline, exactly as Step 10c does.
- `tests/domain/game/test_properties.py` — the `RuleBasedStateMachine`. Its `start` rule must now be followed by a warmup expiry before any answer rule is legal; express that through the machine's guards on the current turn type rather than assuming a question is open.
- `tests/domain/game/test_matrix.py::test_cell[expire-media_warmup]` — should pass once Step 9 registers the state.

Anything else that surfaces here has the same shape and the same fix. Do not weaken an assertion to make it pass: if a test asserted that a question opens at start, the correct edit is to assert that a warmup opens at start and a question opens one expiry later.

- [ ] **Step 11b: Rename the surrender test Task 4 added**

Task 4's `test_surrender_during_expansion_finishes_a_two_player_game` folds `StartGame` and then surrenders — which, after this task, happens during the **warmup** window rather than during an expansion question. The assertions are still exactly right, but the name now lies. Rename it to `test_surrender_during_warmup_finishes_a_two_player_game` and update its docstring's first line to:

```python
    """Spec 1 §3.6: one active player remaining ends the game — including
    before the first question has ever been presented (spec §3.4)."""
```

This is the two-player half of §3.4's requirement; `test_warmup.py`'s three-player test is the other half, asserting the window keeps running when elimination leaves more than one player.

- [ ] **Step 11c: Re-route Task 4's window test through the warmup**

Task 4's `test_a_surrendered_players_answer_does_not_close_the_window` folds `StartGame` and then asserts `isinstance(state.turn, ExpansionQuestion)` before answering — which is now a warmup turn. Insert the expiry between the fold and that assertion:

```python
    state = fold(lobby_state(), decide(lobby_state(), StartGame(PlayerId("p1")), start_ctx()))
    assert state.turn is not None
    state = fold(
        state,
        decide(
            state,
            ExpireDeadline(state.turn.deadline.id),
            DecisionContext(now=NOW + timedelta(minutes=1)),
        ),
    )
    assert isinstance(state.turn, ExpansionQuestion)
    window = state.turn.deadline.id
```

Add `ExpireDeadline`, `DecisionContext`, `NOW`, and `timedelta` to that file's imports as needed. The test's meaning is unchanged — it still asserts that P3 gets their window.

- [ ] **Step 12: Run the linters and the coverage gate**

Run:
```bash
cd backend && uv run pytest -q \
  && uv run ruff check . && uv run ruff format --check . && uv run mypy
```
Expected: PASS. If a branch in `_close_media_warmup` or the new `_apply` arm is uncovered, add the missing case to `test_warmup.py` rather than lowering the gate.

- [ ] **Step 13: Commit**

```bash
git add backend/src/triviador/domain/game backend/tests/domain/game
git commit -m "feat(domain): add the MediaWarmup window before the first question"
```

---

### Task 6: Genesis — `create_initial_state` and `map_sha256`

**Files:**
- Create: `backend/src/triviador/domain/maps/digest.py`
- Create: `backend/src/triviador/domain/game/genesis.py`
- Modify: `backend/src/triviador/domain/game/events.py` (`GameCreated`)
- Modify: `backend/src/triviador/domain/game/reducer.py` (`_apply` genesis arm, stale comment)
- Modify: `backend/src/triviador/maps/registry.py` (`LoadedMap`, `load_with_digest`)
- Test: `backend/tests/domain/maps/test_digest.py` (create)
- Test: `backend/tests/domain/game/test_genesis.py` (create)
- Test: `backend/tests/maps/test_registry.py` (modify)

**Interfaces:**
- Produces:
  - `canonical_digest(raw: object) -> str` — sha256 hex of the canonical JSON serialization.
  - `GameCreated(map_id, rules, host_id, map_sha256: str)`.
  - `create_initial_state(event: GameCreated, game_id: GameId, map_defn: MapDefinition) -> GameState` — returns `seq=1`.
  - `GenesisEventNotFoldable(Exception)`.
  - `LoadedMap(definition: MapDefinition, sha256: str)` and `MapRegistry.load_with_digest(map_id) -> LoadedMap`.
- Consumed by Plan 4 (`GameManager` recovery) and Plan 3 (the event codec).

Spec §2 A-6: `_apply` has no `GameCreated` arm, and the comment at `reducer.py:1107-1116` justifies that by pointing at `lobby_state()` — which exists only as a **test fixture** (`tests/conftest.py:123`). There is no production genesis constructor, so "recovery folds the log" is not currently implementable.

- [ ] **Step 1: Write the failing digest test**

Create `backend/tests/domain/maps/test_digest.py`:

```python
"""Canonical map digest. Formatting must not change the hash; content must."""

from triviador.domain.maps.digest import canonical_digest


def test_key_order_and_whitespace_do_not_change_the_digest() -> None:
    a = {"map_id": "t", "regions": [{"id": "a", "name": "A"}]}
    b = {"regions": [{"name": "A", "id": "a"}], "map_id": "t"}
    assert canonical_digest(a) == canonical_digest(b)


def test_a_content_change_changes_the_digest() -> None:
    a = {"adjacency": {"a": ["b"], "b": ["a"]}}
    b = {"adjacency": {"a": ["c"], "c": ["a"]}}
    assert canonical_digest(a) != canonical_digest(b)


def test_list_order_is_significant() -> None:
    """Adjacency lists are ordered in the file; a reordering is a real edit as
    far as this digest is concerned. Being strict here is deliberate — a false
    positive costs one operator confirmation, a false negative silently replays
    a game against different adjacency."""
    assert canonical_digest({"x": ["a", "b"]}) != canonical_digest({"x": ["b", "a"]})


def test_non_ascii_names_are_stable() -> None:
    value = {"name": "Královéhradecký"}
    assert canonical_digest(value) == canonical_digest({"name": "Královéhradecký"})
    assert len(canonical_digest(value)) == 64
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && uv run pytest tests/domain/maps/test_digest.py -v --no-cov`
Expected: FAIL with `ModuleNotFoundError: No module named 'triviador.domain.maps.digest'`.

- [ ] **Step 3: Write the digest**

Create `backend/src/triviador/domain/maps/digest.py`:

```python
"""Content digest for map topology.

Pure: hashes a value already in memory. The *reading* of map.json lives in
`triviador.maps.registry`, outside the domain.
"""

import hashlib
import json


def canonical_digest(raw: object) -> str:
    """sha256 of the canonical JSON serialization of `raw`.

    Canonical, not the file's bytes: reformatting map.json must not read as a
    map change, or every cosmetic edit would refuse to load every historical
    game that used it.
    """
    canonical = json.dumps(raw, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
```

- [ ] **Step 4: Run the digest tests**

Run: `cd backend && uv run pytest tests/domain/maps/test_digest.py -v --no-cov`
Expected: PASS.

- [ ] **Step 5: Write the failing genesis test**

Create `backend/tests/domain/game/test_genesis.py`:

```python
"""Genesis: GameCreated is consumed, never folded."""

import pytest

from tests.conftest import grid_map
from triviador.domain.game import events as ev
from triviador.domain.game.genesis import GenesisEventNotFoldable, create_initial_state
from triviador.domain.game.reducer import evolve, fold
from triviador.domain.game.rules import DEFAULT_RULES
from triviador.domain.game.state import GameState, Phase
from triviador.domain.ids import GameId, MapId, PlayerId

CREATED = ev.GameCreated(
    map_id=MapId("grid"),
    rules=DEFAULT_RULES,
    host_id=PlayerId("p1"),
    map_sha256="0" * 64,
)


def a_state() -> GameState:
    return create_initial_state(CREATED, GameId("g1"), grid_map())


def test_genesis_produces_an_empty_lobby_at_seq_one() -> None:
    state = a_state()
    assert state.seq == 1, "GameCreated is seq 1; last_seq=0 is only a pre-insert value"
    assert state.phase is Phase.LOBBY
    assert state.players == {}
    assert state.turn_order == ()
    assert state.turn is None
    assert state.winner_id is None
    assert state.round_no == 0
    assert state.next_deadline_id == 1


def test_genesis_seeds_one_unowned_territory_per_region() -> None:
    state = a_state()
    assert set(state.territories) == set(grid_map().region_ids())
    assert all(t.owner_id is None for t in state.territories.values())
    assert state.free_regions() == grid_map().region_ids()


def test_genesis_carries_the_rules_and_the_map() -> None:
    state = a_state()
    assert state.rules == DEFAULT_RULES
    assert state.map == grid_map()
    assert state.game_id == GameId("g1")


def test_the_pool_starts_empty() -> None:
    state = a_state()
    assert state.pool.numeric == ()
    assert state.pool.multiple_choice == ()


def test_folding_game_created_is_refused() -> None:
    """ADR-004 reads 'log + map registry -> state'. GameCreated is the genesis:
    consumed by create_initial_state, never replayed through evolve."""
    with pytest.raises(GenesisEventNotFoldable):
        evolve(a_state(), CREATED)


def test_recovery_is_genesis_then_fold() -> None:
    """The shape Plan 4's recovery uses: construct from events[0], fold the
    rest."""
    log = [CREATED, ev.PlayerJoined(PlayerId("p1"), "One", seat=0)]
    state = fold(create_initial_state(log[0], GameId("g1"), grid_map()), log[1:])
    assert state.players[PlayerId("p1")].display_name == "One"
    assert state.seq == 2
```

- [ ] **Step 6: Run test to verify it fails**

Run: `cd backend && uv run pytest tests/domain/game/test_genesis.py -v --no-cov`
Expected: FAIL with `ModuleNotFoundError: No module named 'triviador.domain.game.genesis'`.

- [ ] **Step 7: Add `map_sha256` to `GameCreated`**

In `backend/src/triviador/domain/game/events.py`, replace `GameCreated`:

```python
@dataclass(frozen=True)
class GameCreated:
    """The genesis event. Consumed by `create_initial_state`, never folded.

    `map_sha256` pins the topology this game was created against. Maps are a
    two-file drop with no version and no migration, so a silent edit is the
    expected failure mode; recovery recomputes this and refuses to load the
    game on mismatch rather than replaying against different adjacency.
    """

    map_id: MapId
    rules: GameRules
    host_id: PlayerId
    map_sha256: str
```

- [ ] **Step 8: Write the genesis constructor**

Create `backend/src/triviador/domain/game/genesis.py`:

```python
"""Where a GameState comes from.

`GameCreated` is a genesis event: it is *consumed* to build the initial state,
never folded through `evolve`. Recovery is therefore

    create_initial_state(events[0], game_id, map_defn)
    fold(that, events[1:])

which is what makes ADR-004 ("the event log is the truth") read literally, with
the map registry supplying the one immutable input the log references by id.
"""

from triviador.domain.game import events as ev
from triviador.domain.game.state import GameState, Phase, Territory, TerritoryKind
from triviador.domain.ids import GameId
from triviador.domain.maps.definition import MapDefinition
from triviador.domain.questions.types import QuestionPool


class GenesisEventNotFoldable(Exception):
    """A genesis event was handed to `evolve`. Use `create_initial_state`."""


def create_initial_state(
    event: ev.GameCreated, game_id: GameId, map_defn: MapDefinition
) -> GameState:
    """Build the empty lobby a game starts from.

    `seq=1` because `GameCreated` *is* sequence 1: creation writes the `games`
    row and the genesis event in one transaction, so `last_seq=0` exists only
    as a pre-insert value and never as a persisted row.
    """
    return GameState(
        game_id=game_id,
        seq=1,
        next_deadline_id=1,
        map=map_defn,
        rules=event.rules,
        phase=Phase.LOBBY,
        round_no=0,
        turn_order=(),
        players={},
        territories={
            region_id: Territory(
                region_id=region_id,
                owner_id=None,
                kind=TerritoryKind.NORMAL,
                base_owner_id=None,
                base_hp=None,
                acquisition=None,
            )
            for region_id in map_defn.region_ids()
        },
        turn=None,
        pool=QuestionPool(numeric=(), multiple_choice=()),
        winner_id=None,
    )
```

- [ ] **Step 9: Refuse to fold the genesis event**

In `backend/src/triviador/domain/game/reducer.py`, add to the imports:

```python
from triviador.domain.game.genesis import GenesisEventNotFoldable
```

Add an explicit arm at the **top** of `_apply`'s `match event:` block, before `case ev.PlayerJoined(...)`:

```python
        case ev.GameCreated():
            raise GenesisEventNotFoldable(
                "GameCreated is a genesis event — use create_initial_state()"
            )
```

Then replace the long comment above the final `raise NotImplementedError` (the block from `# Every `ev.X(...)` construction site` down to `# dead queue is a separate, operational concern.`) with:

```python
    # Every event `decide()` can emit has a `case` above; `GameCreated` has an
    # explicit refusing arm because it is genesis, not a transition. This
    # fallthrough is therefore unreachable for any sequence `decide()` produced
    # — it fires only if `evolve`/`fold` is handed a fabricated or foreign event.
```

- [ ] **Step 10: Run the genesis tests**

Run: `cd backend && uv run pytest tests/domain/game/test_genesis.py -v --no-cov`
Expected: PASS.

- [ ] **Step 11: Expose the digest from the registry**

In `backend/src/triviador/maps/registry.py`, add the import and the return type, and split `load`:

```python
from triviador.domain.maps.digest import canonical_digest


@dataclass(frozen=True)
class LoadedMap:
    definition: MapDefinition
    sha256: str
```

Then restructure `MapRegistry` so the parsing happens once:

```python
    def load(self, map_id: MapId) -> MapDefinition:
        return self.load_with_digest(map_id).definition

    def load_with_digest(self, map_id: MapId) -> LoadedMap:
        path = self.root / map_id / "map.json"
        if not path.is_file():
            raise InvalidMapError(f"map {map_id!r}: no map.json at {path}")

        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise InvalidMapError(f"map {map_id!r}: malformed JSON — {exc}") from exc

        try:
            defn = MapDefinition(
                map_id=MapId(raw["map_id"]),
                regions=tuple(Region(RegionId(r["id"]), r["name"]) for r in raw["regions"]),
                adjacency={
                    RegionId(k): frozenset(RegionId(n) for n in v)
                    for k, v in raw["adjacency"].items()
                },
            )
        except (KeyError, TypeError, AttributeError) as exc:
            raise InvalidMapError(f"map {map_id!r}: structurally invalid — {exc}") from exc

        problems = validate_map(defn)
        if problems:
            raise InvalidMapError(f"map {map_id!r} is invalid: " + "; ".join(problems))
        return LoadedMap(defn, canonical_digest(raw))
```

`load()` keeps its signature so every existing caller and test is untouched.

- [ ] **Step 12: Test the registry digest**

Append to `backend/tests/maps/test_registry.py`:

```python
def test_load_with_digest_returns_a_stable_sha256() -> None:
    first = MapRegistry(REPO_MAPS).load_with_digest(MapId("czechia"))
    second = MapRegistry(REPO_MAPS).load_with_digest(MapId("czechia"))
    assert first.sha256 == second.sha256
    assert len(first.sha256) == 64
    assert first.definition == second.definition


def test_load_still_returns_a_bare_definition() -> None:
    registry = MapRegistry(REPO_MAPS)
    assert registry.load(MapId("czechia")) == registry.load_with_digest(MapId("czechia")).definition


def test_reformatting_map_json_does_not_change_the_digest(tmp_path: Path) -> None:
    """Canonical digest, not file bytes: a whitespace-only edit must not
    invalidate every historical game that used this map."""
    source = json.loads((REPO_MAPS / "czechia" / "map.json").read_text(encoding="utf-8"))
    original = MapRegistry(REPO_MAPS).load_with_digest(MapId("czechia")).sha256

    reformatted = tmp_path / "czechia"
    reformatted.mkdir()
    (reformatted / "map.json").write_text(json.dumps(source, indent=4, sort_keys=True))

    assert MapRegistry(tmp_path).load_with_digest(MapId("czechia")).sha256 == original
```

Replace the `_R` alias in the first test with the already-imported `MapRegistry`; it is written that way here only to keep the snippet self-contained. Confirm `json`, `Path`, and `MapId` are already imported at the top of the file — they are.

- [ ] **Step 13: Run the full suite, the linters, and the coverage gate**

Run:
```bash
cd backend && uv run pytest -q \
  && uv run ruff check . && uv run ruff format --check . && uv run mypy
```
Expected: PASS. Any construction of `ev.GameCreated` that predates this task now fails type checking for the missing `map_sha256` — there should be none outside `test_genesis.py`; confirm with `grep -rn "GameCreated(" backend/`.

- [ ] **Step 14: Commit**

```bash
git add backend/src/triviador/domain backend/src/triviador/maps backend/tests
git commit -m "feat(domain): add genesis state construction and map content digest"
```

---

## Done criteria

```
uv run pytest -q                                        all green, 100 % branch
uv run ruff check . && uv run ruff format --check .     clean
uv run mypy                                             Success
```

`pyproject.toml`'s `addopts` already adds `--cov`, and `[tool.coverage.report] fail_under = 100` scopes the gate to `reducer.py`, so a plain full-suite run enforces it — there is no separate coverage command to remember. The same wiring is why every **targeted** run in this plan passes `--no-cov`: without it, a subset run measures near-zero coverage and fails the gate even when every test in it passed.

And, specifically:

- a lobby departure frees its seat number for the next joiner
- `AbortGame()` with no actor clears an empty lobby
- `StartGame` opens a `MediaWarmup` window and presents no question
- expiring that window starts round one and opens the first answer deadline
- a surrender leaving one active player finishes the game, in any turn shape
- a surrendered player's answer no longer closes an expansion window early
- `create_initial_state` builds a `seq=1` empty lobby, and `evolve` refuses `GameCreated`
- `MapRegistry.load_with_digest` returns a digest stable across reformatting

## What this plan does not do

`GameCreated.map_sha256` is **produced** here but neither **populated** nor **verified** yet:

- Plan 5's `POST /api/games` calls `load_with_digest()` and writes the resulting `sha256` into the genesis event it commits (spec §6.2).
- Plan 4's `GameManager` recomputes it on recovery and refuses to load the game on mismatch (spec §3.2), which is where map loading happens.

Likewise `AbortGame()` gains its system form here but acquires its caller — the reaper's abandoned-lobby sweep — in Plan 4.
