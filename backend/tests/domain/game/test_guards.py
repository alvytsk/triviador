from dataclasses import replace

import pytest

from tests.conftest import NOW, lobby_state
from triviador.domain.game.actions import (
    AbortGame,
    DecisionContext,
    ExpireDeadline,
    PickRegion,
    RejectCode,
    RejectedCommand,
    SubmitAnswer,
)
from triviador.domain.game.reducer import decide
from triviador.domain.game.state import ChoiceAnswer, Phase
from triviador.domain.ids import DeadlineId, PlayerId, RegionId

CTX = DecisionContext(now=NOW)


def test_terminal_phase_ignores_everything() -> None:
    for phase in (Phase.FINISHED, Phase.ABORTED):
        state = replace(lobby_state(), phase=phase)
        assert (
            decide(state, SubmitAnswer(PlayerId("p1"), DeadlineId(1), ChoiceAnswer(0), 100), CTX)
            == ()
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
    command = SubmitAnswer(PlayerId("p1"), DeadlineId(1), ChoiceAnswer(0), 10)
    assert decide(state, command, CTX) == ()
