from dataclasses import replace
from datetime import timedelta
from decimal import Decimal

import pytest

from tests.conftest import NOW, expire_warmup, lobby_state
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
    Surrender,
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
    state = expire_warmup(state)
    for player, guess in ((P1, 100), (P2, 110), (P3, 120)):
        assert isinstance(state.turn, ExpansionQuestion)
        cmd = SubmitAnswer(player, state.turn.deadline.id, NumericAnswer(Decimal(guess)))
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


def test_auto_pick_forfeits_an_eliminated_pickers_remaining_grants() -> None:
    """Regression (fix review, item 4/property machine): surrender during
    EXPANSION never touches the open turn (`_is_involved_in_turn` only knows
    about BATTLE turn shapes), so `current_picker` can go stale. Before the
    fix, a timeout on this window handed p1's region to p1 anyway, even
    though they were already eliminated and neutralized — the Hypothesis
    machine caught this within a handful of steps once `surrender` was wired
    up as a rule."""
    state = picking_state()
    assert isinstance(state.turn, ExpansionPicking)
    assert state.turn.current_picker == P1
    window = state.turn.deadline.id
    state = fold(state, decide(state, Surrender(P1), CTX))
    assert isinstance(state.turn, ExpansionPicking)
    assert state.turn.current_picker == P1  # still stale: the turn was untouched

    late = DecisionContext(now=NOW + timedelta(seconds=60))
    events = decide(state, ExpireDeadline(window), late)
    assert not any(isinstance(e, ev.TerritoryClaimed) for e in events)
    granted = next(e for e in events if isinstance(e, ev.PicksGranted))
    assert granted.grants[P1] == 0
    after = fold(state, events)
    assert isinstance(after.turn, ExpansionPicking)
    assert after.turn.current_picker == P2


def test_picking_skips_a_stale_grant_left_by_a_non_current_eliminated_picker() -> None:
    """Regression (task 21): `_apply`'s `PicksGranted` handler used to
    recompute `current_picker` by scanning `grants` for the first positive
    entry with no elimination filter, disagreeing with `_next_picker` (which
    the earlier `test_auto_pick_forfeits_...` test exercises, but only for
    the case where the *current* picker is the one who was eliminated).
    Here p2 — who is neither the current picker nor the one being acted
    on — surrenders mid-round, leaving `grants_remaining[p2] == 1` stale.
    p1 then claims their own pick, and `_next_picker` (used by both decide()
    and `_apply`) must skip the eliminated p2 and land on p3, not install
    p2 as `current_picker`."""
    state = picking_state({"claims_by_rank": (1, 1, 1)})
    assert isinstance(state.turn, ExpansionPicking)
    assert state.turn.pick_order == (P1, P2, P3)
    assert state.turn.current_picker == P1

    state = fold(state, decide(state, Surrender(P2), CTX))
    assert isinstance(state.turn, ExpansionPicking)
    assert state.turn.current_picker == P1  # untouched: surrender doesn't revisit the turn
    assert state.turn.grants_remaining[P2] == 1  # stale positive grant

    events = decide(state, PickRegion(P1, state.turn.deadline.id, RegionId("r1")), CTX)
    granted = next(e for e in events if isinstance(e, ev.PicksGranted))
    assert granted.grants[P2] == 1  # still un-scrubbed on the event itself
    after = fold(state, events)
    assert isinstance(after.turn, ExpansionPicking)
    assert after.turn.current_picker == P3


def test_surrender_finishes_the_game_when_it_leaves_the_last_active_picker() -> None:
    """Regression (Task 4, domain amendments): this used to be
    "test_auto_pick_finishes_the_game_when_every_remaining_picker_is_eliminated",
    which drove the very same two-surrender trajectory but then relied on the
    *stale window later expiring* — `_advance_expansion`'s own "one active
    player left" guard was the only thing that caught it, because
    `_decide_surrender` never checked for an endgame condition on the
    EXPANSION path at all (Spec 1 §3.6, Defect A). That guard is gone now:
    `_decide_surrender` finishes the game the instant the second surrender
    drops active players to one, before the open window ever gets a chance
    to expire. See `test_auto_pick_forfeits_a_surrendered_pickers_grant_...`
    below for the sibling trajectory that still reaches `_advance_expansion`
    via a stale-window forfeit, because it leaves two players active."""
    state = picking_state({"expansion_rounds": 1})
    assert isinstance(state.turn, ExpansionPicking)
    state = fold(state, decide(state, Surrender(P1), CTX))
    assert isinstance(state.turn, ExpansionPicking), "two players still active: window stays open"
    assert state.active_players() == (P2, P3)

    events = decide(state, Surrender(P2), CTX)
    assert any(isinstance(e, ev.GameFinished) for e in events)
    after = fold(state, events)
    assert after.phase is Phase.FINISHED
    assert after.winner_id == P3
    assert after.turn is None


def test_auto_pick_forfeits_a_surrendered_pickers_grant_with_two_players_left() -> None:
    """`_decide_auto_pick`'s forfeit branch (a picker's window outlives their
    surrender, so `ExpireDeadline` finds `current_picker` already eliminated)
    can find zero eligible pickers left *without* ending the game: unlike
    `test_surrender_finishes_the_game_when_it_leaves_the_last_active_picker`,
    this leaves two players active, so `_advance_expansion` must fall
    through to its normal round-advance path (here, straight to BATTLE,
    since this is the last expansion round) instead of the "one active
    player left" case `_decide_surrender` now claims exclusively for itself."""
    state = picking_state({"claims_by_rank": (1, 1, 0), "expansion_rounds": 1})
    assert isinstance(state.turn, ExpansionPicking)
    assert state.turn.pick_order == (P1, P2)
    state = fold(state, decide(state, PickRegion(P1, state.turn.deadline.id, RegionId("r1")), CTX))
    assert isinstance(state.turn, ExpansionPicking)
    assert state.turn.current_picker == P2
    window = state.turn.deadline.id

    state = fold(state, decide(state, Surrender(P2), CTX))
    assert state.active_players() == (P1, P3)

    late = DecisionContext(now=NOW + timedelta(seconds=60))
    events = decide(state, ExpireDeadline(window), late)
    kinds = [type(e) for e in events]
    assert ev.ExpansionRoundCompleted in kinds
    assert ev.GameFinished not in kinds
    after = fold(state, events)
    assert after.phase is Phase.BATTLE


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
            cmd = SubmitAnswer(player, state.turn.deadline.id, NumericAnswer(Decimal(guess)))
            state = fold(state, decide(state, cmd, CTX))
    assert state.phase is Phase.BATTLE
    assert state.free_regions() == ()
    assert isinstance(state.turn, BattleTargetSelect)
    assert state.turn.attacker_id == state.active_players()[0]
