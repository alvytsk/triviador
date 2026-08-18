from dataclasses import replace
from datetime import timedelta

import pytest

from tests.conftest import NOW
from tests.domain.game.test_target_select import CTX, P1, P2, battle_state, open_turn
from triviador.domain.game import events as ev
from triviador.domain.game.actions import (
    DecisionContext,
    ExpireDeadline,
    RejectCode,
    RejectedCommand,
    SelectAttackTarget,
    SubmitAnswer,
)
from triviador.domain.game.reducer import decide, fold
from triviador.domain.game.scoring import holding_value
from triviador.domain.game.state import (
    AcquisitionKind,
    ChoiceAnswer,
    GameState,
    NeutralChallenge,
    SubmittedAnswer,
)
from triviador.domain.ids import RegionId

NEUTRAL = RegionId("r4")
DUEL_FREE_EVENTS = (ev.DuelResolved, ev.DefenseHeld, ev.BaseDamaged, ev.TiebreakStarted)


def challenging() -> GameState:
    state = open_turn(battle_state())
    assert state.turn is not None
    return fold(state, decide(state, SelectAttackTarget(P1, state.turn.deadline.id, NEUTRAL), CTX))


def answer(state: GameState, idx: int) -> SubmitAnswer:
    assert isinstance(state.turn, NeutralChallenge)
    return SubmitAnswer(P1, state.turn.deadline.id, ChoiceAnswer(idx))


def test_correct_answer_captures_the_region() -> None:
    state = challenging()
    events = decide(state, answer(state, 0), CTX)  # mc questions are correct at index 0
    assert [type(e) for e in events][:5] == [
        ev.AnswerSubmitted,
        ev.AnswerWindowClosed,
        ev.QuestionResolved,
        ev.NeutralTerritoryCaptured,
        ev.ScoreChanged,
    ]


def test_capture_is_claimed_not_conquest() -> None:
    state = challenging()
    after = fold(state, decide(state, answer(state, 0), CTX))
    territory = after.territories[NEUTRAL]
    assert territory.owner_id == P1
    assert territory.acquisition is AcquisitionKind.CLAIMED
    assert holding_value(territory, after.rules) == after.rules.pts_territory


def test_wrong_answer_fails_and_leaves_the_region_neutral() -> None:
    state = challenging()
    events = decide(state, answer(state, 1), CTX)
    assert any(isinstance(e, ev.NeutralAttackFailed) for e in events)
    after = fold(state, events)
    assert after.territories[NEUTRAL].owner_id is None


def test_timeout_behaves_like_a_wrong_answer() -> None:
    state = challenging()
    assert isinstance(state.turn, NeutralChallenge)
    late = DecisionContext(now=NOW + timedelta(seconds=60))
    events = decide(state, ExpireDeadline(state.turn.deadline.id), late)
    assert any(isinstance(e, ev.NeutralAttackFailed) for e in events)
    assert fold(state, events).territories[NEUTRAL].owner_id is None


def test_a_neutral_challenge_is_never_reported_as_a_duel() -> None:
    for idx in (0, 1):
        state = challenging()
        events = decide(state, answer(state, idx), CTX)
        assert not any(isinstance(e, DUEL_FREE_EVENTS) for e in events)


def test_only_the_attacker_may_answer_a_neutral_challenge() -> None:
    from triviador.domain.game.reducer import LEGAL_COMMANDS

    state = challenging()
    assert isinstance(state.turn, NeutralChallenge)
    assert state.turn.attacker_id == P1
    assert SubmitAnswer in LEGAL_COMMANDS[NeutralChallenge]


def test_a_bystander_cannot_answer_someone_elses_neutral_challenge() -> None:
    state = challenging()
    assert isinstance(state.turn, NeutralChallenge)
    bystander_answer = SubmitAnswer(P2, state.turn.deadline.id, ChoiceAnswer(0))
    with pytest.raises(RejectedCommand) as exc:
        decide(state, bystander_answer, CTX)
    assert exc.value.code is RejectCode.NOT_YOUR_TURN


def test_repeating_the_same_neutral_answer_is_ignored() -> None:
    """A `NeutralChallenge` resolves atomically on the attacker's first (and
    only) answer, so `decide` never itself hands back a state where
    `turn.answers` is already populated — `_record_answer`'s idempotent-
    resubmission guard is exercised directly here instead, the same guard
    the multi-answerer turn shapes reach naturally by waiting on a second
    player."""
    state = challenging()
    assert isinstance(state.turn, NeutralChallenge)
    already_answered = replace(state.turn, answers={P1: SubmittedAnswer(ChoiceAnswer(0), 400)})
    doctored = replace(state, turn=already_answered)
    assert decide(doctored, answer(doctored, 0), CTX) == ()
