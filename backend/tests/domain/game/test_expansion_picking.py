from dataclasses import replace
from datetime import timedelta
from decimal import Decimal

import pytest

from tests.conftest import NOW, lobby_state
from tests.domain.game.test_start import P1, P2, P3, start_ctx
from triviador.domain.game import events as ev
from triviador.domain.game.actions import (
    DecisionContext,
    ExpireDeadline,
    PickRegion,
    RejectCode,
    RejectedCommand,
    StartGame,
    SubmitAnswer,
)
from triviador.domain.game.reducer import decide, fold
from triviador.domain.game.state import (
    AcquisitionKind,
    BattleTargetSelect,
    ExpansionPicking,
    ExpansionQuestion,
    GameState,
    NumericAnswer,
    Phase,
)
from triviador.domain.ids import RegionId

CTX = DecisionContext(now=NOW)


def picking_state(rules_override: dict[str, object] | None = None) -> GameState:
    base = lobby_state()
    if rules_override:
        base = replace(base, rules=replace(base.rules, **rules_override))  # type: ignore[arg-type]
    state = fold(base, decide(base, StartGame(P1), start_ctx()))
    for player, guess in ((P1, 100), (P2, 110), (P3, 120)):
        assert isinstance(state.turn, ExpansionQuestion)
        cmd = SubmitAnswer(player, state.turn.deadline.id, NumericAnswer(Decimal(guess)), 100)
        state = fold(state, decide(state, cmd, CTX))
    return state


def test_picking_a_free_region_claims_and_scores_it() -> None:
    state = picking_state()
    assert isinstance(state.turn, ExpansionPicking)
    # Under DEFAULT_RULES's claims_by_rank=(2, 1, 0), the rank-0 player (p1
    # here) is granted two picks, so this first pick still leaves p1 with one
    # grant remaining and a PicksGranted event opening its own fresh window
    # (see test_each_pick_opens_a_fresh_window).
    events = decide(state, PickRegion(P1, state.turn.deadline.id, RegionId("r1")), CTX)
    assert [type(e) for e in events] == [ev.TerritoryClaimed, ev.ScoreChanged, ev.PicksGranted]
    claimed = events[0]
    assert isinstance(claimed, ev.TerritoryClaimed)
    assert claimed.acquisition is AcquisitionKind.CLAIMED
    assert claimed.automatic is False


def test_picking_an_owned_region_is_rejected() -> None:
    state = picking_state()
    assert isinstance(state.turn, ExpansionPicking)
    with pytest.raises(RejectedCommand) as exc:
        decide(state, PickRegion(P1, state.turn.deadline.id, RegionId("r0")), CTX)
    assert exc.value.code is RejectCode.REGION_NOT_FREE


def test_picking_an_unknown_region_is_rejected() -> None:
    state = picking_state()
    assert isinstance(state.turn, ExpansionPicking)
    with pytest.raises(RejectedCommand) as exc:
        decide(state, PickRegion(P1, state.turn.deadline.id, RegionId("nope")), CTX)
    assert exc.value.code is RejectCode.UNKNOWN_REGION


def test_picking_out_of_turn_is_rejected() -> None:
    state = picking_state()
    assert isinstance(state.turn, ExpansionPicking)
    with pytest.raises(RejectedCommand) as exc:
        decide(state, PickRegion(P2, state.turn.deadline.id, RegionId("r1")), CTX)
    assert exc.value.code is RejectCode.NOT_YOUR_TURN


def test_each_pick_opens_a_fresh_window() -> None:
    state = picking_state()
    assert isinstance(state.turn, ExpansionPicking)
    first_window = state.turn.deadline.id
    state = fold(state, decide(state, PickRegion(P1, first_window, RegionId("r1")), CTX))
    assert isinstance(state.turn, ExpansionPicking)
    assert state.turn.current_picker == P1, "p1 was granted two picks"
    assert state.turn.deadline.id != first_window


def test_auto_pick_with_a_stale_shuffle_advances_instead_of_claiming() -> None:
    """`shuffled_region_ids` is the runtime's snapshot; if none of it is still
    free by the time the timer fires, auto-pick has nothing legal to claim
    and falls through to `_advance_expansion` rather than crashing or
    inventing a target."""
    state = picking_state()
    assert isinstance(state.turn, ExpansionPicking)
    ctx = DecisionContext(
        now=NOW + timedelta(seconds=60),
        shuffled_region_ids=(RegionId("r0"), RegionId("r2")),  # both bases: never free
    )
    events = decide(state, ExpireDeadline(state.turn.deadline.id), ctx)
    assert not any(isinstance(e, ev.TerritoryClaimed) for e in events)
    assert isinstance(events[0], ev.ExpansionRoundCompleted)
    after = fold(state, events)
    assert after.round_no == 2
    assert isinstance(after.turn, ExpansionQuestion)


def test_timeout_auto_picks_from_the_shuffled_order() -> None:
    state = picking_state()
    assert isinstance(state.turn, ExpansionPicking)
    ctx = DecisionContext(
        now=NOW + timedelta(seconds=60),
        shuffled_region_ids=(RegionId("r8"), RegionId("r7")),
    )
    events = decide(state, ExpireDeadline(state.turn.deadline.id), ctx)
    claimed = next(e for e in events if isinstance(e, ev.TerritoryClaimed))
    assert claimed.region_id == RegionId("r8")
    assert claimed.automatic is True


def test_finishing_all_picks_starts_the_next_expansion_round() -> None:
    state = picking_state()
    for region in ("r1", "r3", "r4"):  # p1 x2 then p2 x1
        assert isinstance(state.turn, ExpansionPicking)
        state = fold(
            state,
            decide(
                state,
                PickRegion(state.turn.current_picker, state.turn.deadline.id, RegionId(region)),
                CTX,
            ),
        )
    assert state.round_no == 2
    assert isinstance(state.turn, ExpansionQuestion)


def test_running_out_of_free_regions_enters_the_battle_stage() -> None:
    # 9 regions: 3 bases + 3 picks per round. Round 2 fills the map.
    state = picking_state()
    for _ in range(2):
        for _ in range(3):
            assert isinstance(state.turn, ExpansionPicking)
            free = state.free_regions()[0]
            state = fold(
                state,
                decide(
                    state, PickRegion(state.turn.current_picker, state.turn.deadline.id, free), CTX
                ),
            )
        if state.phase is Phase.BATTLE:
            break
        assert isinstance(state.turn, ExpansionQuestion)
        for player, guess in ((P1, 100), (P2, 110), (P3, 120)):
            cmd = SubmitAnswer(player, state.turn.deadline.id, NumericAnswer(Decimal(guess)), 100)
            state = fold(state, decide(state, cmd, CTX))
    assert state.phase is Phase.BATTLE
    assert state.free_regions() == ()
    assert isinstance(state.turn, BattleTargetSelect)
    assert state.turn.attacker_id == state.active_players()[0]
