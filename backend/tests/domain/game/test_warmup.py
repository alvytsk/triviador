"""The media warmup window between the pool draw and the first question."""

from datetime import timedelta

from tests.conftest import NOW, lobby_state
from tests.domain.game.test_start import P1, P2, start_ctx
from triviador.domain.game import events as ev
from triviador.domain.game.actions import (
    AbortGame,
    DecisionContext,
    ExpireDeadline,
    StartGame,
    Surrender,
)
from triviador.domain.game.reducer import decide, fold
from triviador.domain.game.state import (
    DeadlineKind,
    ExpansionQuestion,
    GameState,
    MediaWarmup,
    Phase,
)
from triviador.domain.ids import DeadlineId

LATE = DecisionContext(now=NOW + timedelta(minutes=1))


def started() -> GameState:
    base = lobby_state()
    return fold(base, decide(base, StartGame(P1), start_ctx()))


def test_start_opens_a_warmup_window_not_a_question() -> None:
    state = started()
    assert isinstance(state.turn, MediaWarmup)
    assert state.phase is Phase.EXPANSION
    assert state.turn.deadline.kind is DeadlineKind.WARMUP


def test_the_warmup_deadline_is_warmup_ms_after_now() -> None:
    state = started()
    assert isinstance(state.turn, MediaWarmup)
    assert state.turn.deadline.deadline_at == NOW + timedelta(milliseconds=state.rules.warmup_ms)


def test_no_question_is_presented_during_warmup() -> None:
    """The whole point: the pool is drawn and prefetchable, but no answer
    timer is running yet."""
    events = decide(lobby_state(), StartGame(P1), start_ctx())
    assert any(isinstance(e, ev.QuestionPoolDrawn) for e in events)
    assert not any(isinstance(e, ev.QuestionPresented) for e in events)


def window(state: GameState) -> DeadlineId:
    deadline = state.current_deadline()
    assert deadline is not None
    return deadline.id


def test_expiring_the_warmup_starts_round_one_and_presents_a_question() -> None:
    state = started()
    events = decide(state, ExpireDeadline(window(state)), LATE)
    kinds = [type(e) for e in events]
    assert kinds == [ev.ExpansionRoundStarted, ev.QuestionPresented]

    after = fold(state, events)
    assert isinstance(after.turn, ExpansionQuestion)
    assert after.round_no == 1
    assert after.turn.deadline.kind is DeadlineKind.ANSWER
    assert after.pool.numeric_used == 1
    assert after.turn.question.prompt == "numeric 0?"


def test_an_early_warmup_expiry_is_ignored() -> None:
    """Guard 4: the timer fired before its own deadline."""
    state = started()
    assert decide(state, ExpireDeadline(window(state)), DecisionContext(now=NOW)) == ()


def test_a_stale_warmup_expiry_is_ignored() -> None:
    state = started()
    assert decide(state, ExpireDeadline(DeadlineId(999)), LATE) == ()


def test_the_system_can_abort_during_warmup() -> None:
    state = started()
    after = fold(state, decide(state, AbortGame(), LATE))
    assert after.phase is Phase.ABORTED


def test_surrender_during_warmup_eliminates_without_ending_a_three_player_game() -> None:
    state = started()
    after = fold(state, decide(state, Surrender(P2), LATE))
    assert after.players[P2].is_eliminated
    assert after.phase is Phase.EXPANSION
    assert isinstance(after.turn, MediaWarmup), "the warmup window keeps running"
