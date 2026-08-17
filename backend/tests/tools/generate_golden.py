"""Generate `tests/codec/golden/*.json` — the golden corpus.

Run by hand, once per intentional regeneration:

    uv run python tests/tools/generate_golden.py

Not a test: this file lives under `tests/tools/`, not `tests/`, and its name
does not start with `test_`, so pytest never collects it (see
`tests/tools/__init__.py` and the layering note in `golden/README.md`).

Determinism is the whole point of a golden corpus: a fixed clock (`NOW` from
`tests/conftest.py`), a fixed question pool (`full_pool()`, no randomness),
no `uuid`, no wall-clock reads. Running this script twice in a row against
the same commit must produce byte-identical files.

Three trajectories are played end to end through the real `decide`/`fold`
reducer — never hand-built `GameState` objects — then every event is
encoded with the Task 4 codec and written alongside a summary of the final
state. `tests/codec/test_golden_corpus.py` reads these files back, decodes,
folds, and compares against that summary without ever calling `encode`
itself.
"""

import json
import subprocess
import sys
from dataclasses import dataclass
from datetime import timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

# `tests/tools/generate_golden.py` -> backend/. Inserted so `import
# tests.conftest` and `import triviador...` both resolve when this file is
# run directly (`uv run python tests/tools/generate_golden.py`), which does
# not put the backend root on `sys.path` the way pytest does.
ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tests.conftest import NOW, full_pool, grid_map  # noqa: E402
from triviador.db.codec.codec import encode  # noqa: E402
from triviador.domain.game import events as ev  # noqa: E402
from triviador.domain.game.actions import (  # noqa: E402
    AbortGame,
    Command,
    DecisionContext,
    ExpireDeadline,
    JoinGame,
    PickRegion,
    SelectAttackTarget,
    StartGame,
    SubmitAnswer,
    Surrender,
)
from triviador.domain.game.genesis import create_initial_state  # noqa: E402
from triviador.domain.game.reducer import decide, fold  # noqa: E402
from triviador.domain.game.rules import DEFAULT_RULES  # noqa: E402
from triviador.domain.game.state import (  # noqa: E402
    BattleDuel,
    BattleTargetSelect,
    ChoiceAnswer,
    ExpansionPicking,
    ExpansionQuestion,
    GameState,
    MediaWarmup,
    NumericAnswer,
    Phase,
)
from triviador.domain.ids import GameId, PlayerId, RegionId  # noqa: E402
from triviador.domain.maps.definition import MapDefinition  # noqa: E402
from triviador.domain.maps.digest import canonical_digest  # noqa: E402

MAP: MapDefinition = grid_map()

P1, P2, P3 = PlayerId("p1"), PlayerId("p2"), PlayerId("p3")
R0, R1, R2, R3, R4, R5, R6, R7, R8 = (RegionId(f"r{i}") for i in range(9))

GOLDEN_DIR = ROOT / "tests" / "codec" / "golden"


def map_sha256(map_defn: MapDefinition) -> str:
    """A deterministic digest of the shared map builder's output.

    Plan 3 only stores this; Plan 4 verifies it against `map.json` on
    recovery. There is no `map.json` backing `grid_map()` (it's a
    programmatic test fixture), so this hashes a canonical projection of the
    `MapDefinition` itself rather than a file that doesn't exist.
    """
    raw = {
        "map_id": str(map_defn.map_id),
        "regions": [
            {"region_id": str(r.region_id), "display_name": r.display_name}
            for r in map_defn.regions
        ],
        "adjacency": {
            str(region_id): sorted(str(n) for n in neighbours)
            for region_id, neighbours in map_defn.adjacency.items()
        },
    }
    return canonical_digest(raw)


def genesis(game_id: GameId, host_id: PlayerId) -> ev.GameCreated:
    return ev.GameCreated(MAP.map_id, DEFAULT_RULES, host_id, map_sha256(MAP))


@dataclass
class Trajectory:
    name: str
    game_id: GameId
    events: list[ev.GameEvent]
    state: GameState


def _new_trajectory(name: str, game_id: GameId, host_id: PlayerId) -> Trajectory:
    created = genesis(game_id, host_id)
    state = create_initial_state(created, game_id, MAP)
    return Trajectory(name=name, game_id=game_id, events=[created], state=state)


def _step(traj: Trajectory, command: Command, ctx: DecisionContext) -> None:
    new_events = decide(traj.state, command, ctx)
    traj.events.extend(new_events)
    traj.state = fold(traj.state, new_events)


def _start_ctx() -> DecisionContext:
    return DecisionContext(
        now=NOW,
        shuffled_player_ids=(P1, P2, P3),
        base_regions=(R0, R2, R6),
        drawn_pool=full_pool(),
    )


def _expire_open_window(traj: Trajectory) -> None:
    """`ExpireDeadline` the turn's current window, at a time guaranteed to
    be past its deadline."""
    deadline = traj.state.current_deadline()
    assert deadline is not None
    ctx = DecisionContext(now=deadline.deadline_at + timedelta(seconds=1))
    _step(traj, ExpireDeadline(deadline.id), ctx)


# --- trajectory 1: expansion_to_battle --------------------------------------


def build_expansion_to_battle() -> Trajectory:
    """Creation, three joins, start, the `MediaWarmup` window, a full
    two-round expansion phase that fills the 3x3 grid, and three
    battle-round-1 attacks (a capture, a held defense, a mutual miss) that
    carry the game into the opening of battle round 2."""
    ctx = DecisionContext(now=NOW)
    traj = _new_trajectory("expansion_to_battle", GameId("g-expansion-to-battle"), P1)

    _step(traj, JoinGame(P1, "One"), ctx)
    _step(traj, JoinGame(P2, "Two"), ctx)
    _step(traj, JoinGame(P3, "Three"), ctx)
    _step(traj, StartGame(P1), _start_ctx())

    assert isinstance(traj.state.turn, MediaWarmup)
    _expire_open_window(traj)

    # Expansion round 1: p1 guesses closest, p2 next, p3 last.
    assert isinstance(traj.state.turn, ExpansionQuestion)
    window = traj.state.turn.deadline.id
    _step(traj, SubmitAnswer(P1, window, NumericAnswer(Decimal(100)), 300), ctx)
    _step(traj, SubmitAnswer(P2, window, NumericAnswer(Decimal(110)), 400), ctx)
    _step(traj, SubmitAnswer(P3, window, NumericAnswer(Decimal(120)), 500), ctx)

    # claims_by_rank=(2, 1, 0): p1 picks twice, p2 once.
    assert isinstance(traj.state.turn, ExpansionPicking)
    _step(traj, PickRegion(P1, traj.state.turn.deadline.id, R1), ctx)
    assert isinstance(traj.state.turn, ExpansionPicking)
    _step(traj, PickRegion(P1, traj.state.turn.deadline.id, R3), ctx)
    assert isinstance(traj.state.turn, ExpansionPicking)
    _step(traj, PickRegion(P2, traj.state.turn.deadline.id, R4), ctx)

    # Expansion round 2: same ranking, claims the remaining three regions —
    # the map (9 regions, 3 bases) is now full, so this is the last round.
    assert isinstance(traj.state.turn, ExpansionQuestion)
    window = traj.state.turn.deadline.id
    _step(traj, SubmitAnswer(P1, window, NumericAnswer(Decimal(101)), 300), ctx)
    _step(traj, SubmitAnswer(P2, window, NumericAnswer(Decimal(111)), 400), ctx)
    _step(traj, SubmitAnswer(P3, window, NumericAnswer(Decimal(121)), 500), ctx)

    assert isinstance(traj.state.turn, ExpansionPicking)
    _step(traj, PickRegion(P1, traj.state.turn.deadline.id, R5), ctx)
    assert isinstance(traj.state.turn, ExpansionPicking)
    _step(traj, PickRegion(P1, traj.state.turn.deadline.id, R7), ctx)
    assert isinstance(traj.state.turn, ExpansionPicking)
    _step(traj, PickRegion(P2, traj.state.turn.deadline.id, R8), ctx)

    assert traj.state.phase is Phase.BATTLE
    assert traj.state.round_no == 1

    # Battle round 1, turn 1: p1 attacks p2's r4 and wins it (correct vs wrong).
    assert isinstance(traj.state.turn, BattleTargetSelect)
    assert traj.state.turn.attacker_id == P1
    _step(traj, SelectAttackTarget(P1, traj.state.turn.deadline.id, R4), ctx)
    assert isinstance(traj.state.turn, BattleDuel)
    window = traj.state.turn.deadline.id
    _step(traj, SubmitAnswer(P1, window, ChoiceAnswer(0), 300), ctx)
    _step(traj, SubmitAnswer(P2, window, ChoiceAnswer(1), 400), ctx)

    # Turn 2: p2 attacks p1's r5 and fails (wrong vs correct) — defense held.
    assert isinstance(traj.state.turn, BattleTargetSelect)
    assert traj.state.turn.attacker_id == P2
    _step(traj, SelectAttackTarget(P2, traj.state.turn.deadline.id, R5), ctx)
    assert isinstance(traj.state.turn, BattleDuel)
    window = traj.state.turn.deadline.id
    _step(traj, SubmitAnswer(P2, window, ChoiceAnswer(1), 400), ctx)
    _step(traj, SubmitAnswer(P1, window, ChoiceAnswer(0), 300), ctx)

    # Turn 3: p3 attacks p1's r3, both wrong — nothing changes hands.
    assert isinstance(traj.state.turn, BattleTargetSelect)
    assert traj.state.turn.attacker_id == P3
    _step(traj, SelectAttackTarget(P3, traj.state.turn.deadline.id, R3), ctx)
    assert isinstance(traj.state.turn, BattleDuel)
    window = traj.state.turn.deadline.id
    _step(traj, SubmitAnswer(P3, window, ChoiceAnswer(1), 400), ctx)
    _step(traj, SubmitAnswer(P1, window, ChoiceAnswer(1), 300), ctx)

    assert traj.state.phase is Phase.BATTLE
    assert traj.state.round_no == 2
    return traj


# --- trajectory 2: surrender_ends_game --------------------------------------


def build_surrender_ends_game() -> Trajectory:
    """Two consecutive EXPANSION-phase surrenders drop active players to
    one. This exercises the *event sequence* Plan 2's fix for Spec 1 §3.6
    Defect A produces (`_decide_surrender` finishing the game itself the
    instant `active_players() <= 1`, rather than relying on a stale window
    later expiring), and pins how `fold` replays that sequence.

    It does not guard the fix itself: `_apply` only replays the
    `GameFinished` event already baked into the committed rows, so this
    corpus would still pass unchanged even if the fix in
    `_decide_surrender` were reverted. The decide-side guarantee lives in
    `tests/domain/game/test_surrender.py::
    test_surrender_leaving_one_active_player_finishes_the_game`, which
    calls `decide()` directly.
    """
    ctx = DecisionContext(now=NOW)
    traj = _new_trajectory("surrender_ends_game", GameId("g-surrender-ends-game"), P1)

    _step(traj, JoinGame(P1, "One"), ctx)
    _step(traj, JoinGame(P2, "Two"), ctx)
    _step(traj, JoinGame(P3, "Three"), ctx)
    _step(traj, StartGame(P1), _start_ctx())

    assert isinstance(traj.state.turn, MediaWarmup)
    _expire_open_window(traj)

    assert isinstance(traj.state.turn, ExpansionQuestion)
    window = traj.state.turn.deadline.id
    _step(traj, SubmitAnswer(P1, window, NumericAnswer(Decimal(100)), 300), ctx)
    _step(traj, SubmitAnswer(P2, window, NumericAnswer(Decimal(110)), 400), ctx)
    _step(traj, SubmitAnswer(P3, window, NumericAnswer(Decimal(120)), 500), ctx)

    assert isinstance(traj.state.turn, ExpansionPicking)
    _step(traj, Surrender(P2), ctx)
    assert traj.state.active_players() == (P1, P3), "two players still active: the game continues"

    _step(traj, Surrender(P3), ctx)
    assert traj.state.phase is Phase.FINISHED
    assert traj.state.winner_id == P1
    return traj


# --- trajectory 3: abort_from_lobby -----------------------------------------


def build_abort_from_lobby() -> Trajectory:
    """Genesis, one join, a system-authorized abort — the only corpus entry
    covering `AbortGame(actor_id=None)`."""
    ctx = DecisionContext(now=NOW)
    traj = _new_trajectory("abort_from_lobby", GameId("g-abort-from-lobby"), P1)

    _step(traj, JoinGame(P1, "One"), ctx)
    _step(traj, AbortGame(None), ctx)

    assert traj.state.phase is Phase.ABORTED
    return traj


# --- summary + serialization -------------------------------------------------


def summarize(state: GameState) -> dict[str, Any]:
    """Mirrors `tests/codec/test_golden_corpus.py`'s `summarize` exactly.

    Deliberately duplicated rather than imported: the generator is allowed
    to import `encode` (the test module must not), and keeping the two
    files independent means neither can quietly depend on the other's
    internals. Observable state only — phase, round, scores, per-region
    ownership, eliminations, winner, next deadline id — never a serialized
    `GameState`.
    """
    return {
        "seq": state.seq,
        "phase": state.phase.value,
        "round_no": state.round_no,
        "winner_id": state.winner_id,
        "scores": {pid: player.score for pid, player in state.players.items()},
        "territories": {
            rid: {"owner_id": t.owner_id, "kind": t.kind.value, "base_hp": t.base_hp}
            for rid, t in state.territories.items()
        },
        "eliminated": sorted(pid for pid, p in state.players.items() if p.is_eliminated),
        "next_deadline_id": state.next_deadline_id,
    }


def git_sha() -> str:
    result = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True, check=True
    )
    return result.stdout.strip()


def build_doc(traj: Trajectory, sha: str) -> dict[str, Any]:
    rows = []
    for seq, event in enumerate(traj.events, start=1):
        wire_type, version, payload = encode(event)
        rows.append({"seq": seq, "type": wire_type, "schema_version": version, "payload": payload})
    return {
        "name": traj.name,
        "game_id": str(traj.game_id),
        "map_id": str(MAP.map_id),
        "generated_from": sha,
        "rows": rows,
        "expected": summarize(traj.state),
    }


def main() -> None:
    sha = git_sha()
    GOLDEN_DIR.mkdir(parents=True, exist_ok=True)
    for build in (build_expansion_to_battle, build_surrender_ends_game, build_abort_from_lobby):
        traj = build()
        doc = build_doc(traj, sha)
        path = GOLDEN_DIR / f"{traj.name}.json"
        path.write_text(json.dumps(doc, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        print(f"wrote {path} ({len(traj.events)} events)")


if __name__ == "__main__":
    main()
