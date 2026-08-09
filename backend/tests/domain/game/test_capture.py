from dataclasses import replace

from tests.domain.game.test_duel import CORRECT, WRONG, mc
from tests.domain.game.test_target_select import CTX, P1, P2, battle_state, open_turn
from triviador.domain.game import events as ev
from triviador.domain.game.actions import SelectAttackTarget
from triviador.domain.game.reducer import decide, fold
from triviador.domain.game.state import (
    AcquisitionKind,
    GameState,
    Phase,
    TerritoryKind,
)
from triviador.domain.ids import RegionId

TARGET = RegionId("r2")


def with_target(kind: TerritoryKind, hp: int | None, acquisition: AcquisitionKind) -> GameState:
    state = battle_state()
    territory = replace(
        state.territories[TARGET],
        kind=kind,
        base_owner_id=P2 if hp else None,
        base_hp=hp,
        acquisition=acquisition,
    )
    state = replace(state, territories={**state.territories, TARGET: territory})
    p2 = state.players[P2]
    from triviador.domain.game.scoring import expected_score

    state = replace(
        state, players={**state.players, P2: replace(p2, score=expected_score(state, P2))}
    )
    state = open_turn(state)
    assert state.turn is not None
    return fold(state, decide(state, SelectAttackTarget(P1, state.turn.deadline.id, TARGET), CTX))


def win_the_duel(state: GameState) -> tuple[GameState, tuple[ev.GameEvent, ...]]:
    state = fold(state, decide(state, mc(state, P1, CORRECT), CTX))
    events = decide(state, mc(state, P2, WRONG), CTX)
    return fold(state, events), events


def test_normal_region_capture_emits_the_exact_sequence() -> None:
    state = with_target(TerritoryKind.NORMAL, None, AcquisitionKind.CLAIMED)
    after, events = win_the_duel(state)
    kinds = [type(e) for e in events]
    head = kinds.index(ev.TerritoryCaptured)
    assert kinds[head : head + 3] == [ev.TerritoryCaptured, ev.ScoreChanged, ev.ScoreChanged]
    gain, loss = events[head + 1], events[head + 2]
    assert isinstance(gain, ev.ScoreChanged) and isinstance(loss, ev.ScoreChanged)
    assert (gain.player_id, gain.delta, gain.reason) == (
        P1,
        after.rules.pts_conquered,
        ev.ScoreReason.CONQUEST,
    )
    assert (loss.player_id, loss.delta, loss.reason) == (
        P2,
        -after.rules.pts_territory,
        ev.ScoreReason.TERRITORY_LOST,
    )


def test_a_captured_region_is_worth_more_to_its_conqueror() -> None:
    state = with_target(TerritoryKind.NORMAL, None, AcquisitionKind.CLAIMED)
    after, _ = win_the_duel(state)
    assert after.territories[TARGET].acquisition is AcquisitionKind.CONQUEST
    from triviador.domain.game.scoring import holding_value

    assert holding_value(after.territories[TARGET], after.rules) == after.rules.pts_conquered


def test_damaging_a_base_does_not_move_the_region() -> None:
    state = with_target(TerritoryKind.BASE, 3, AcquisitionKind.BASE)
    after, events = win_the_duel(state)
    damaged = next(e for e in events if isinstance(e, ev.BaseDamaged))
    assert damaged.hp_remaining == 2
    assert not any(isinstance(e, ev.TerritoryCaptured) for e in events)
    assert after.territories[TARGET].owner_id == P2
    assert after.territories[TARGET].base_hp == 2


def test_destroying_the_last_tower_transfers_the_base_and_eliminates() -> None:
    state = with_target(TerritoryKind.BASE, 1, AcquisitionKind.BASE)
    after, events = win_the_duel(state)
    kinds = [type(e) for e in events]
    assert ev.BaseDestroyed in kinds
    assert kinds.index(ev.BaseDestroyed) < kinds.index(ev.TerritoryCaptured)
    assert ev.PlayerEliminated in kinds
    assert after.territories[TARGET].owner_id == P1
    assert after.players[P2].is_eliminated is True


def test_every_other_holding_of_the_eliminated_player_becomes_neutral() -> None:
    state = with_target(TerritoryKind.BASE, 1, AcquisitionKind.BASE)
    after, events = win_the_duel(state)
    # p2 also owned r5; it must be neutral, not inherited by p1.
    assert any(isinstance(e, ev.TerritoryNeutralized) for e in events)
    assert after.territories[RegionId("r5")].owner_id is None
    assert after.territories[RegionId("r5")].acquisition is None
    assert after.owned_by(P2) == ()


def test_elimination_never_removes_accumulated_bonuses() -> None:
    state = with_target(TerritoryKind.BASE, 1, AcquisitionKind.BASE)
    p2 = state.players[P2]
    state = replace(
        state, players={**state.players, P2: replace(p2, bonus_score=300, score=p2.score + 300)}
    )
    after, _ = win_the_duel(state)
    assert after.players[P2].bonus_score == 300
    assert after.players[P2].score == 300


def test_one_active_player_remaining_finishes_the_game() -> None:
    layout = {
        "r0": "p1",
        "r1": "p1",
        "r3": "p1",
        "r4": "p1",
        "r6": "p1",
        "r7": "p1",
        "r8": "p1",
        "r2": "p2",
        "r5": "p2",
    }
    state = battle_state(layout)
    territory = replace(
        state.territories[TARGET],
        kind=TerritoryKind.BASE,
        base_owner_id=P2,
        base_hp=1,
        acquisition=AcquisitionKind.BASE,
    )
    state = replace(
        state,
        territories={**state.territories, TARGET: territory},
        turn_order=(P1, P2),
        players={P1: state.players[P1], P2: state.players[P2]},
    )
    state = open_turn(state)
    assert state.turn is not None
    state = fold(state, decide(state, SelectAttackTarget(P1, state.turn.deadline.id, TARGET), CTX))
    after, events = win_the_duel(state)
    assert any(isinstance(e, ev.GameFinished) for e in events)
    assert after.phase is Phase.FINISHED
    assert after.winner_id == P1
