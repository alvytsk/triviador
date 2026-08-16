"""AbortGame, player-issued and system-issued."""

import pytest

from tests.conftest import NOW, lobby_state
from triviador.domain.game import events as ev
from triviador.domain.game.actions import (
    AbortGame,
    DecisionContext,
    RejectCode,
    RejectedCommand,
)
from triviador.domain.game.reducer import decide, fold
from triviador.domain.game.state import Phase
from triviador.domain.ids import PlayerId

CTX = DecisionContext(now=NOW)


def test_a_player_can_abort_their_own_game() -> None:
    state = lobby_state()
    events = decide(state, AbortGame(PlayerId("p1")), CTX)
    assert events == (ev.GameAborted("aborted by p1"),)
    assert fold(state, events).phase is Phase.ABORTED


def test_a_non_participant_cannot_abort() -> None:
    state = lobby_state()
    with pytest.raises(RejectedCommand) as exc:
        decide(state, AbortGame(PlayerId("stranger")), CTX)
    assert exc.value.code is RejectCode.NOT_A_PARTICIPANT


def test_the_system_can_abort_an_empty_lobby() -> None:
    """The reaper's case: an abandoned lobby has no participants at all, so an
    actor-issued abort can never clear it — guard 3 would reject every possible
    actor."""
    state = lobby_state(players={})
    events = decide(state, AbortGame(), CTX)
    assert events == (ev.GameAborted("aborted by system"),)
    assert fold(state, events).phase is Phase.ABORTED


def test_the_system_can_abort_a_populated_lobby() -> None:
    state = lobby_state()
    assert decide(state, AbortGame(), CTX) == (ev.GameAborted("aborted by system"),)
