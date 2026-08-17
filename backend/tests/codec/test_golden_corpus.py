"""Committed event rows must keep decoding and folding to the same state.

This is the one test that can catch a semantic change to how the reducer
*applies* an event (`evolve`/`_apply`). It does not catch a bug in what
`decide()` computes, because `_apply` only interprets recorded event data —
with one exception: `_apply` delegates to `_next_picker`, a helper it shares
with the decide side (see `reducer.py`'s `PicksGranted` branch), so a change
there is visible to this corpus even though it originates on the decide
side. The domain's `decide()`-calling unit tests under `tests/domain/game/`
remain the primary guard for game logic; this is a second, narrower layer on
top of them.

Read only: nothing here calls `encode`. A test that encodes and then decodes
its own output would only prove the codec agrees with itself, which is true
of every broken codec too.

Pure and PostgreSQL-free: no `integration` marker, no asyncio marks.
"""

import json
from pathlib import Path
from typing import Any

import pytest

from tests.conftest import grid_map
from triviador.db.codec.codec import decode
from triviador.domain.game.events import GameCreated
from triviador.domain.game.genesis import create_initial_state
from triviador.domain.game.reducer import fold
from triviador.domain.game.state import GameState
from triviador.domain.ids import GameId
from triviador.domain.maps.definition import MapDefinition

# The golden corpus was generated against the shared map builder in
# `tests/conftest.py` (Controller ruling R6) — not a second, hand-rolled map
# definition. `tests/tools/generate_golden.py` resolves the same builder.
MAP: MapDefinition = grid_map()

CORPUS = sorted((Path(__file__).parent / "golden").glob("*.json"))


def summarize(state: GameState) -> dict[str, Any]:
    """Deliberately not a serialized `GameState`.

    A whole-object snapshot breaks on every field addition and trains people
    to regenerate the corpus without reading the diff, which destroys the
    guard this test exists to provide. This projects only the observable
    state — phase, round, per-player scores, per-region ownership/kind/hp,
    who is eliminated, the winner, and the next deadline id — and stays
    stable under a purely additive change to `GameState`.
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


@pytest.mark.parametrize("path", CORPUS, ids=lambda p: p.stem)
def test_corpus_decodes_and_folds_to_the_expected_state(path: Path) -> None:
    doc = json.loads(path.read_text(encoding="utf-8"))
    assert doc["map_id"] == MAP.map_id
    events = [decode(r["type"], r["schema_version"], r["payload"]) for r in doc["rows"]]
    first = events[0]
    assert isinstance(first, GameCreated)
    state = create_initial_state(first, GameId(doc["game_id"]), MAP)
    state = fold(state, events[1:])
    assert summarize(state) == doc["expected"]


def test_the_corpus_is_not_empty() -> None:
    """A glob that silently matches nothing is a test suite that passes by
    finding no work to do."""
    assert len(CORPUS) == 3
