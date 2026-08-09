from dataclasses import replace
from datetime import timedelta
from decimal import Decimal

import pytest

from tests.conftest import NOW
from tests.domain.game.test_target_select import CTX, P1, P2, P3, battle_state, open_turn
from triviador.domain.game import events as ev
from triviador.domain.game.actions import (
    DecisionContext,
    ExpireDeadline,
    RejectCode,
    RejectedCommand,
    SelectAttackTarget,
    SubmitAnswer,
)
from triviador.domain.game.reducer import _rank_numeric, decide, fold
from triviador.domain.game.state import (
    BattleDuel,
    BattleTiebreak,
    ChoiceAnswer,
    GameState,
    NumericAnswer,
    SubmittedAnswer,
)
from triviador.domain.ids import PlayerId, RegionId

TARGET = RegionId("r2")  # owned by p2, adjacent to p1's r1
CORRECT, WRONG = 0, 1


def dueling() -> GameState:
    state = open_turn(battle_state())
    assert state.turn is not None
    return fold(state, decide(state, SelectAttackTarget(P1, state.turn.deadline.id, TARGET), CTX))


def mc(state: GameState, player: PlayerId, idx: int, elapsed: int = 300) -> SubmitAnswer:
    assert isinstance(state.turn, BattleDuel | BattleTiebreak)
    return SubmitAnswer(player, state.turn.deadline.id, ChoiceAnswer(idx), elapsed)


def both(
    state: GameState, attacker_idx: int, defender_idx: int
) -> tuple[GameState, tuple[ev.GameEvent, ...]]:
    state = fold(state, decide(state, mc(state, P1, attacker_idx), CTX))
    events = decide(state, mc(state, P2, defender_idx), CTX)
    return fold(state, events), events


def test_attacker_right_defender_wrong_captures() -> None:
    _, events = both(dueling(), CORRECT, WRONG)
    resolved = next(e for e in events if isinstance(e, ev.DuelResolved))
    assert resolved.winner_id == P1
    assert any(isinstance(e, ev.TerritoryCaptured) for e in events)


def test_attacker_wrong_defender_right_holds_and_scores_defense() -> None:
    after, events = both(dueling(), WRONG, CORRECT)
    resolved = next(e for e in events if isinstance(e, ev.DuelResolved))
    assert resolved.winner_id == P2
    assert any(isinstance(e, ev.DefenseHeld) for e in events)
    bonus = next(
        e for e in events if isinstance(e, ev.ScoreChanged) and e.reason is ev.ScoreReason.DEFENSE
    )
    assert bonus.delta == after.rules.pts_defense
    assert after.players[P2].bonus_score == after.rules.pts_defense
    assert after.territories[TARGET].owner_id == P2


def test_both_wrong_changes_nothing() -> None:
    after, events = both(dueling(), WRONG, WRONG)
    resolved = next(e for e in events if isinstance(e, ev.DuelResolved))
    assert resolved.winner_id is None
    assert not any(isinstance(e, ev.TerritoryCaptured | ev.DefenseHeld) for e in events)
    assert after.territories[TARGET].owner_id == P2


def test_both_right_opens_a_numeric_tiebreak() -> None:
    after, events = both(dueling(), CORRECT, CORRECT)
    assert any(isinstance(e, ev.TiebreakStarted) for e in events)
    assert isinstance(after.turn, BattleTiebreak)
    assert after.turn.question.prompt.startswith("numeric")
    assert not any(isinstance(e, ev.DuelResolved) for e in events)


def numeric(state: GameState, player: PlayerId, value: int, elapsed: int) -> SubmitAnswer:
    assert isinstance(state.turn, BattleTiebreak)
    return SubmitAnswer(player, state.turn.deadline.id, NumericAnswer(Decimal(value)), elapsed)


def test_closer_tiebreak_guess_wins_the_region() -> None:
    state, _ = both(dueling(), CORRECT, CORRECT)
    assert isinstance(state.turn, BattleTiebreak)
    correct = state.turn.question.numeric_answer
    assert correct is not None
    state = fold(state, decide(state, numeric(state, P1, int(correct), 300), CTX))
    events = decide(state, numeric(state, P2, int(correct) + 50, 200), CTX)
    assert any(isinstance(e, ev.TerritoryCaptured) for e in events)
    assert fold(state, events).territories[TARGET].owner_id == P1


def test_equal_distance_is_broken_by_speed() -> None:
    state, _ = both(dueling(), CORRECT, CORRECT)
    assert isinstance(state.turn, BattleTiebreak)
    numeric_answer = state.turn.question.numeric_answer
    assert numeric_answer is not None
    correct = int(numeric_answer)
    state = fold(state, decide(state, numeric(state, P1, correct + 10, 900), CTX))
    events = decide(state, numeric(state, P2, correct - 10, 100), CTX)
    assert any(isinstance(e, ev.DefenseHeld) for e in events), "faster defender holds"


def test_mutual_silence_in_a_tiebreak_favours_the_defender() -> None:
    state, _ = both(dueling(), CORRECT, CORRECT)
    assert isinstance(state.turn, BattleTiebreak)
    late = DecisionContext(now=NOW + timedelta(seconds=60))
    events = decide(state, ExpireDeadline(state.turn.deadline.id), late)
    assert any(isinstance(e, ev.DefenseHeld) for e in events)
    assert fold(state, events).territories[TARGET].owner_id == P2


def test_defense_bonus_survives_losing_every_territory() -> None:
    after, _ = both(dueling(), WRONG, CORRECT)
    stripped = {
        r: replace(t, owner_id=None, acquisition=None) for r, t in after.territories.items()
    }
    after = replace(after, territories=stripped)
    from triviador.domain.game.scoring import expected_score

    assert expected_score(after, P2) == after.rules.pts_defense


# --- Controller ruling: non-combatants must not influence a battle duel/tiebreak --


def test_non_combatant_cannot_submit_into_a_duel() -> None:
    """Ruling (b): reject at the door. p3 is active but neither attacker nor
    defender in this duel; submitting into it is not a benign race, it's an
    attempt to influence a fight that isn't theirs."""
    state = dueling()
    with pytest.raises(RejectedCommand) as exc:
        decide(state, mc(state, P3, CORRECT), CTX)
    assert exc.value.code is RejectCode.NOT_YOUR_TURN


def test_non_combatant_cannot_submit_into_a_tiebreak() -> None:
    """Same ruling, but for the numeric tiebreak turn shape."""
    state, _ = both(dueling(), CORRECT, CORRECT)
    assert isinstance(state.turn, BattleTiebreak)
    with pytest.raises(RejectedCommand) as exc:
        decide(state, numeric(state, P3, 0, 300), CTX)
    assert exc.value.code is RejectCode.NOT_YOUR_TURN


def test_bystander_answer_does_not_influence_tiebreak_ranking() -> None:
    """Ruling (a): even if a bystander's answer somehow ended up recorded on
    a BattleTiebreak turn, `_rank_numeric` must rank only the two combatants.
    A bystander who "answered best" must never bump the attacker out of first
    place and veto a capture they legitimately won."""
    state, _ = both(dueling(), CORRECT, CORRECT)
    assert isinstance(state.turn, BattleTiebreak)
    turn = state.turn
    correct = turn.question.numeric_answer
    assert correct is not None
    # p1 (attacker) is closest among the real combatants; p3 (a bystander) is
    # closer still, but must not be eligible to rank at all.
    doctored = replace(
        turn,
        answers={
            P1: SubmittedAnswer(NumericAnswer(correct + 5), 300),
            P2: SubmittedAnswer(NumericAnswer(correct + 20), 300),
            P3: SubmittedAnswer(NumericAnswer(correct), 100),
        },
    )
    ranking = _rank_numeric(doctored, state)
    assert set(ranking) == {P1, P2}
    assert ranking[0] == P1
