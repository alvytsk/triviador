from dataclasses import replace

from tests.conftest import lobby_state
from tests.domain.game.test_duel import dueling
from tests.domain.game.test_target_select import CTX, P1, P2, P3, battle_state, open_turn
from triviador.domain.game import events as ev
from triviador.domain.game.actions import AbortGame, Surrender
from triviador.domain.game.reducer import _next_battle_turn, decide, fold
from triviador.domain.game.state import (
    AcquisitionKind,
    BattleTargetSelect,
    FinalTiebreak,
    Phase,
    TerritoryKind,
)
from triviador.domain.ids import RegionId


def with_p1_base() -> object:
    state = battle_state()
    base = replace(
        state.territories[RegionId("r0")],
        kind=TerritoryKind.BASE,
        base_owner_id=P1,
        base_hp=3,
        acquisition=AcquisitionKind.BASE,
    )
    state = replace(state, territories={**state.territories, RegionId("r0"): base})
    p1 = state.players[P1]
    from triviador.domain.game.scoring import expected_score

    state = replace(
        state,
        players={
            **state.players,
            P1: replace(p1, base_region=RegionId("r0"), score=expected_score(state, P1)),
        },
    )
    return open_turn(state)


def test_surrender_in_the_lobby_is_just_leaving() -> None:
    state = lobby_state()
    assert decide(state, Surrender(P1), CTX) == (ev.PlayerLeft(P1),)


def test_the_current_attacker_surrendering_aborts_the_turn_and_advances() -> None:
    state = with_p1_base()
    events = decide(state, Surrender(P1), CTX)  # type: ignore[arg-type]
    kinds = [type(e) for e in events]
    assert kinds[0] is ev.PlayerSurrendered
    assert ev.PlayerEliminated in kinds
    assert ev.TerritoryNeutralized in kinds
    assert ev.TurnAborted in kinds
    assert kinds.index(ev.TurnAborted) < kinds.index(ev.TurnStarted)
    after = fold(state, events)  # type: ignore[arg-type]
    assert isinstance(after.turn, BattleTargetSelect)
    assert after.turn.attacker_id == P2


def test_a_surrendering_players_own_base_is_neutralized_not_awarded() -> None:
    state = with_p1_base()
    after = fold(state, decide(state, Surrender(P1), CTX))  # type: ignore[arg-type]
    base = after.territories[RegionId("r0")]
    assert base.owner_id is None
    assert base.acquisition is None
    assert after.owned_by(P1) == ()


def test_a_duel_defender_surrendering_discards_the_question() -> None:
    state = dueling()  # p1 attacking p2's r2
    events = decide(state, Surrender(P2), CTX)
    assert not any(isinstance(e, ev.DuelResolved | ev.TerritoryCaptured) for e in events)
    assert any(isinstance(e, ev.TurnAborted) for e in events)


def test_surrender_keeps_accumulated_bonuses() -> None:
    state = with_p1_base()
    p1 = state.players[P1]  # type: ignore[attr-defined]
    state = replace(  # type: ignore[type-var]
        state,
        players={
            **state.players,  # type: ignore[attr-defined]
            P1: replace(p1, bonus_score=300, score=p1.score + 300),
        },
    )
    after = fold(state, decide(state, Surrender(P1), CTX))  # type: ignore[arg-type]
    assert after.players[P1].bonus_score == 300
    assert after.players[P1].score == 300


def test_surrender_leaving_one_active_player_finishes_the_game() -> None:
    state = battle_state()
    p3 = state.players[P3]
    state = replace(state, players={**state.players, P3: replace(p3, is_eliminated=True)})
    state = open_turn(state)
    events = decide(state, Surrender(P1), CTX)
    assert any(isinstance(e, ev.GameFinished) for e in events)
    after = fold(state, events)
    assert after.phase is Phase.FINISHED
    assert after.winner_id == P2


def test_surrender_during_a_final_tiebreak_is_ignored() -> None:
    """HUMAN RULING: a contender cannot eliminate themselves mid-tiebreak by
    surrendering. `Surrender` stays legal in `LEGAL_COMMANDS[FinalTiebreak]`
    (Task 10), but `_decide_surrender` must silently drop it — no events, no
    elimination, no `TurnAborted` — rather than reject it outright."""
    state = battle_state()
    tied = {P1: 500, P2: 500, P3: 100}
    state = replace(
        state,
        players={p: replace(s, score=tied[p]) for p, s in state.players.items()},
        round_no=state.rules.battle_rounds,
    )
    state = fold(state, _next_battle_turn(state, CTX))
    assert isinstance(state.turn, FinalTiebreak)
    assert decide(state, Surrender(P1), CTX) == ()
    assert fold(state, decide(state, Surrender(P1), CTX)) == state


def test_abort_works_from_any_non_terminal_phase() -> None:
    for state in (lobby_state(), open_turn(battle_state())):
        events = decide(state, AbortGame(P1), CTX)
        assert [type(e) for e in events] == [ev.GameAborted]
        after = fold(state, events)
        assert after.phase is Phase.ABORTED
        assert after.winner_id is None
        assert after.turn is None
