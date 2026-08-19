"""§8.3: the server is authoritative on time, including how fast an answer was.

`_rank_numeric` breaks ties on `elapsed_ms`. While that number came from the
command, a client reporting 0 won every tie it entered — every expansion
ranking, every battle tiebreak, and the final tiebreak that can decide the
match. The command no longer carries it.
"""

from collections.abc import Callable
from datetime import datetime, timedelta
from decimal import Decimal

import pytest

from tests.conftest import NOW, expire_warmup, full_pool, lobby_state
from triviador.domain.game.actions import (
    DecisionContext,
    StartGame,
    SubmitAnswer,
)
from triviador.domain.game.events import AnswerSubmitted
from triviador.domain.game.reducer import decide, fold
from triviador.domain.game.rules import DEFAULT_RULES
from triviador.domain.game.state import ExpansionQuestion, GameState, NumericAnswer
from triviador.domain.ids import DeadlineId, PlayerId, RegionId

DEADLINE = DeadlineId(1)


def question_open() -> tuple[GameState, datetime]:
    """A state with an ExpansionQuestion window open, and the instant it opened."""
    state = lobby_state()
    started = fold(
        state,
        decide(
            state,
            StartGame(PlayerId("p1")),
            DecisionContext(
                now=NOW,
                shuffled_player_ids=(PlayerId("p1"), PlayerId("p2"), PlayerId("p3")),
                base_regions=(RegionId("r0"), RegionId("r2"), RegionId("r6")),
                drawn_pool=full_pool(),
            ),
        ),
    )
    state = expire_warmup(started)
    assert isinstance(state.turn, ExpansionQuestion)
    opened_at = state.turn.deadline.deadline_at - timedelta(
        milliseconds=state.rules.answer_timeout_ms
    )
    return state, opened_at


def test_submit_answer_no_longer_carries_an_elapsed_time() -> None:
    with pytest.raises(TypeError):
        SubmitAnswer(  # type: ignore[call-arg]
            actor_id=PlayerId("p1"),
            deadline_id=DEADLINE,
            value=NumericAnswer(Decimal(1)),
            elapsed_ms=0,
        )


def test_the_recorded_elapsed_time_is_measured_from_the_window_opening() -> None:
    state, opened_at = question_open()
    assert isinstance(state.turn, ExpansionQuestion)
    events = decide(
        state,
        SubmitAnswer(PlayerId("p1"), state.turn.deadline.id, NumericAnswer(Decimal(7))),
        DecisionContext(now=opened_at + timedelta(milliseconds=1234)),
    )
    submitted = next(e for e in events if isinstance(e, AnswerSubmitted))
    assert submitted.answer.elapsed_ms == 1234


def test_an_answer_at_1001ms_is_recorded_exactly() -> None:
    """`total_seconds() * 1000` truncated through a float lands on 1000 here,
    not 1001, because the float representation of 1.001 seconds falls just
    below the integer millisecond boundary. Exact integer division does not
    have that failure mode."""
    state, opened_at = question_open()
    assert isinstance(state.turn, ExpansionQuestion)
    events = decide(
        state,
        SubmitAnswer(PlayerId("p1"), state.turn.deadline.id, NumericAnswer(Decimal(7))),
        DecisionContext(now=opened_at + timedelta(milliseconds=1001)),
    )
    submitted = next(e for e in events if isinstance(e, AnswerSubmitted))
    assert submitted.answer.elapsed_ms == 1001


def test_an_answer_at_the_very_start_of_the_window_records_zero() -> None:
    state, opened_at = question_open()
    assert isinstance(state.turn, ExpansionQuestion)
    events = decide(
        state,
        SubmitAnswer(PlayerId("p1"), state.turn.deadline.id, NumericAnswer(Decimal(7))),
        DecisionContext(now=opened_at),
    )
    submitted = next(e for e in events if isinstance(e, AnswerSubmitted))
    assert submitted.answer.elapsed_ms == 0


def test_a_clock_that_appears_to_run_backwards_still_records_a_sane_elapsed() -> None:
    """`ctx.now` before the window opened cannot happen from a wall clock,
    but it can from a recovered deadline whose `deadline_at` was written by
    a differently-skewed process. A negative elapsed would sort *ahead* of
    every honest answer — the exact cheat this task removes — so it clamps."""
    state, opened_at = question_open()
    assert isinstance(state.turn, ExpansionQuestion)
    events = decide(
        state,
        SubmitAnswer(PlayerId("p1"), state.turn.deadline.id, NumericAnswer(Decimal(7))),
        DecisionContext(now=opened_at - timedelta(seconds=5)),
    )
    submitted = next(e for e in events if isinstance(e, AnswerSubmitted))
    assert submitted.answer.elapsed_ms == 0


def test_an_answer_landing_after_the_deadline_clamps_to_the_full_window() -> None:
    """The window is still open — nothing has expired it yet — so the answer
    counts, but it can never be recorded as slower than the window was long,
    or the tiebreak key stops being comparable across windows."""
    state, opened_at = question_open()
    assert isinstance(state.turn, ExpansionQuestion)
    events = decide(
        state,
        SubmitAnswer(PlayerId("p1"), state.turn.deadline.id, NumericAnswer(Decimal(7))),
        DecisionContext(now=opened_at + timedelta(seconds=999)),
    )
    submitted = next(e for e in events if isinstance(e, AnswerSubmitted))
    assert submitted.answer.elapsed_ms == DEFAULT_RULES.answer_timeout_ms


def test_the_faster_of_two_equally_wrong_answers_still_wins() -> None:
    """The property `_rank_numeric` actually depends on, now that neither
    player can assert their own speed."""
    state, opened_at = question_open()
    assert isinstance(state.turn, ExpansionQuestion)
    window = state.turn.deadline.id
    at: Callable[[int], DecisionContext] = lambda ms: DecisionContext(  # noqa: E731
        now=opened_at + timedelta(milliseconds=ms)
    )
    state = fold(
        state,
        decide(
            state,
            SubmitAnswer(PlayerId("p2"), window, NumericAnswer(Decimal(10))),
            at(3000),
        ),
    )
    state = fold(
        state,
        decide(
            state,
            SubmitAnswer(PlayerId("p1"), window, NumericAnswer(Decimal(10))),
            at(500),
        ),
    )
    assert isinstance(state.turn, ExpansionQuestion)
    answers = state.turn.answers
    assert answers[PlayerId("p1")].elapsed_ms < answers[PlayerId("p2")].elapsed_ms
