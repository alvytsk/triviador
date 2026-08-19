from dataclasses import replace
from datetime import timedelta

import pytest

from tests.conftest import NOW, full_pool, lobby_state, numeric_question
from triviador.domain.game.actions import (
    AbortGame,
    DecisionContext,
    ExpireDeadline,
    PickRegion,
    RejectCode,
    RejectedCommand,
    StartGame,
    SubmitAnswer,
)
from triviador.domain.game.reducer import _present_question, decide, fold
from triviador.domain.game.state import ChoiceAnswer, Deadline, DeadlineKind, Phase
from triviador.domain.ids import DeadlineId, PlayerId, RegionId

CTX = DecisionContext(now=NOW)


def test_terminal_phase_ignores_everything() -> None:
    for phase in (Phase.FINISHED, Phase.ABORTED):
        state = replace(lobby_state(), phase=phase)
        assert (
            decide(state, SubmitAnswer(PlayerId("p1"), DeadlineId(1), ChoiceAnswer(0)), CTX) == ()
        )
        assert decide(state, ExpireDeadline(DeadlineId(1)), CTX) == ()


def test_terminal_phase_rejects_abort() -> None:
    state = replace(lobby_state(), phase=Phase.FINISHED)
    with pytest.raises(RejectedCommand) as exc:
        decide(state, AbortGame(PlayerId("p1")), CTX)
    assert exc.value.code is RejectCode.WRONG_TURN_STATE


def test_stale_window_is_ignored_not_rejected() -> None:
    state = lobby_state()  # turn is None, so no window matches
    assert decide(state, PickRegion(PlayerId("p1"), DeadlineId(99), RegionId("r0")), CTX) == ()


def test_stale_window_is_checked_before_actor_validity() -> None:
    """A stale packet from a non-participant must be silent, not an error."""
    state = lobby_state()
    ghost = PlayerId("nobody")
    assert decide(state, PickRegion(ghost, DeadlineId(99), RegionId("r0")), CTX) == ()


def test_non_participant_in_the_current_window_is_rejected() -> None:
    state = lobby_state()
    with pytest.raises(RejectedCommand) as exc:
        decide(state, AbortGame(PlayerId("nobody")), CTX)
    assert exc.value.code is RejectCode.NOT_A_PARTICIPANT


def test_windowed_command_with_no_open_window_is_silent_not_rejected() -> None:
    """Guard 2 fires before guard 5: with turn=None there is no window to match."""
    state = lobby_state()
    command = SubmitAnswer(PlayerId("p1"), DeadlineId(1), ChoiceAnswer(0))
    assert decide(state, command, CTX) == ()


def test_expiring_a_deadline_before_it_is_due_is_ignored() -> None:
    """Guard 4: a timer that fired early (clock skew, a duplicate scheduled
    timer) must be a benign no-op, not an error — the real timer still owns
    the resolution."""
    from tests.domain.game.test_start import P1, start_ctx

    base = lobby_state()
    state = fold(base, decide(base, StartGame(P1), start_ctx()))
    assert state.turn is not None
    early = DecisionContext(now=state.turn.deadline.deadline_at - timedelta(seconds=1))
    assert decide(state, ExpireDeadline(state.turn.deadline.id), early) == ()


def test_present_question_rejects_a_phase_with_no_question_window_shape() -> None:
    """`_present_question` is only ever reached from `_apply` while the game
    is EXPANSION or BATTLE — every `QuestionPresented` producer folds the
    phase-changing event first. Exercised directly since no legal command
    sequence can hand it any other phase."""
    state = replace(lobby_state(), phase=Phase.LOBBY, pool=full_pool())
    deadline = Deadline(DeadlineId(1), DeadlineKind.ANSWER, NOW)
    with pytest.raises(NotImplementedError):
        _present_question(state, numeric_question(0, 100), deadline)
