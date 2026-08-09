from dataclasses import replace
from datetime import timedelta

import pytest

from tests.conftest import NOW, full_pool, lobby_state, own
from triviador.domain.game import events as ev
from triviador.domain.game.actions import (
    DecisionContext,
    ExpireDeadline,
    RejectCode,
    RejectedCommand,
    SelectAttackTarget,
)
from triviador.domain.game.reducer import decide, fold, legal_targets
from triviador.domain.game.state import (
    BattleDuel,
    BattleTargetSelect,
    GameState,
    NeutralChallenge,
    Phase,
)
from triviador.domain.ids import PlayerId, RegionId

P1, P2, P3 = PlayerId("p1"), PlayerId("p2"), PlayerId("p3")
CTX = DecisionContext(now=NOW)
LAYOUT = {
    "r0": "p1",
    "r1": "p1",
    "r3": "p1",
    "r2": "p2",
    "r5": "p2",
    "r6": "p3",
    "r7": "p3",
    "r8": "p3",
}


def battle_state(layout: dict[str, str] | None = None) -> GameState:
    state = replace(lobby_state(), phase=Phase.BATTLE, round_no=1, pool=full_pool())
    for region, player in (layout if layout is not None else LAYOUT).items():
        state = own(state, region, player)
    return state


def open_turn(state: GameState, attacker: PlayerId = P1) -> GameState:
    from triviador.domain.game.reducer import _open_battle_turn

    return fold(state, _open_battle_turn(state, attacker, CTX))


def test_legal_targets_are_adjacent_and_not_mine() -> None:
    # p1 owns r0, r1, r3. Their neighbours are r2, r4, r6 (minus p1's own).
    assert set(legal_targets(battle_state(), P1)) == {
        RegionId("r2"),
        RegionId("r4"),
        RegionId("r6"),
    }


def test_legal_targets_is_empty_when_everything_adjacent_is_mine() -> None:
    solo = {f"r{i}": "p1" for i in range(9)}
    assert legal_targets(battle_state(solo), P1) == ()


def test_no_legal_target_skips_the_turn_without_opening_a_window() -> None:
    from triviador.domain.game.reducer import _open_battle_turn

    solo = {f"r{i}": "p1" for i in range(9)}
    events = _open_battle_turn(battle_state(solo), P1, CTX)
    assert isinstance(events[0], ev.TurnSkipped)
    assert not any(isinstance(e, ev.TurnStarted) for e in events)


def test_selecting_an_owned_enemy_region_opens_a_duel() -> None:
    state = open_turn(battle_state())
    assert isinstance(state.turn, BattleTargetSelect)
    events = decide(state, SelectAttackTarget(P1, state.turn.deadline.id, RegionId("r2")), CTX)
    assert [type(e) for e in events] == [ev.AttackDeclared, ev.QuestionPresented]
    after = fold(state, events)
    assert isinstance(after.turn, BattleDuel)
    assert after.turn.defender_id == P2
    assert after.turn.question.prompt.startswith("mc")


def test_selecting_a_neutral_region_opens_a_challenge_not_a_duel() -> None:
    state = open_turn(battle_state())
    assert isinstance(state.turn, BattleTargetSelect)
    events = decide(state, SelectAttackTarget(P1, state.turn.deadline.id, RegionId("r4")), CTX)
    declared = events[0]
    assert isinstance(declared, ev.AttackDeclared)
    assert declared.defender_id is None
    after = fold(state, events)
    assert isinstance(after.turn, NeutralChallenge)


def test_selecting_a_non_adjacent_region_is_rejected() -> None:
    state = open_turn(battle_state())
    assert isinstance(state.turn, BattleTargetSelect)
    with pytest.raises(RejectedCommand) as exc:
        decide(state, SelectAttackTarget(P1, state.turn.deadline.id, RegionId("r8")), CTX)
    assert exc.value.code is RejectCode.NOT_ADJACENT


def test_selecting_my_own_region_is_rejected() -> None:
    state = open_turn(battle_state())
    assert isinstance(state.turn, BattleTargetSelect)
    with pytest.raises(RejectedCommand) as exc:
        decide(state, SelectAttackTarget(P1, state.turn.deadline.id, RegionId("r1")), CTX)
    assert exc.value.code is RejectCode.OWN_TERRITORY


def test_selecting_an_unknown_region_is_rejected() -> None:
    state = open_turn(battle_state())
    assert isinstance(state.turn, BattleTargetSelect)
    with pytest.raises(RejectedCommand) as exc:
        decide(state, SelectAttackTarget(P1, state.turn.deadline.id, RegionId("nope")), CTX)
    assert exc.value.code is RejectCode.UNKNOWN_REGION


def test_selecting_out_of_turn_is_rejected() -> None:
    state = open_turn(battle_state())
    assert isinstance(state.turn, BattleTargetSelect)
    with pytest.raises(RejectedCommand) as exc:
        decide(state, SelectAttackTarget(P2, state.turn.deadline.id, RegionId("r4")), CTX)
    assert exc.value.code is RejectCode.NOT_YOUR_TURN


def test_target_timeout_skips_the_turn_and_advances() -> None:
    state = open_turn(battle_state())
    assert isinstance(state.turn, BattleTargetSelect)
    late = DecisionContext(now=NOW + timedelta(seconds=60))
    events = decide(state, ExpireDeadline(state.turn.deadline.id), late)
    assert isinstance(events[0], ev.TurnSkipped)
    after = fold(state, events)
    assert isinstance(after.turn, BattleTargetSelect)
    assert after.turn.attacker_id == P2


def test_consecutive_skips_terminate_instead_of_recursing_forever() -> None:
    """Regression: `evolve` treats `TurnSkipped` as a no-op, so `state.turn`
    stays the stale `BattleTargetSelect` for the *original* attacker (p1)
    across the whole skip chain. Before the fix, `_next_battle_turn` kept
    recomputing the same "next attacker" (p2) from that stale anchor forever
    whenever p2 also had no legal target — infinite recursion. p2 owns no
    territory at all here, so `legal_targets(p2)` is trivially empty and the
    chain must stop after skipping p2, without ever reaching p3.
    """
    layout = {
        "r0": "p1",
        "r1": "p1",
        "r3": "p1",
        "r2": "p3",
        "r4": "p3",
        "r5": "p3",
        "r6": "p3",
        "r7": "p3",
        "r8": "p3",
    }
    assert legal_targets(battle_state(layout), P2) == ()

    state = open_turn(battle_state(layout), P1)
    assert isinstance(state.turn, BattleTargetSelect)
    late = DecisionContext(now=NOW + timedelta(seconds=60))
    events = decide(state, ExpireDeadline(state.turn.deadline.id), late)

    first, second = events
    assert isinstance(first, ev.TurnSkipped)
    assert isinstance(second, ev.TurnSkipped)
    assert first.attacker_id == P1
    assert second.attacker_id == P2

    # Folding the chain doesn't crash either, and no third attacker is ever
    # reached — Task 18 owns advancing past a stuck anchor.
    after = fold(state, events)
    assert after.turn is not None
