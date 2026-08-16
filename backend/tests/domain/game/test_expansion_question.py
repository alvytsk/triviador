from dataclasses import replace
from datetime import timedelta
from decimal import Decimal

import pytest

from tests.conftest import NOW, expire_warmup, lobby_state
from tests.domain.game.test_start import P1, P2, P3, start_ctx
from triviador.domain.game import events as ev
from triviador.domain.game.actions import (
    DecisionContext,
    ExpireDeadline,
    RejectCode,
    RejectedCommand,
    StartGame,
    SubmitAnswer,
    Surrender,
)
from triviador.domain.game.reducer import decide, fold
from triviador.domain.game.rules import DEFAULT_RULES
from triviador.domain.game.state import (
    ChoiceAnswer,
    ExpansionPicking,
    ExpansionQuestion,
    GameState,
    NumericAnswer,
)
from triviador.domain.ids import PlayerId


def started() -> GameState:
    base = lobby_state()
    state = fold(base, decide(base, StartGame(P1), start_ctx()))
    return expire_warmup(state)


def answer(state: GameState, player: PlayerId, value: int, elapsed: int) -> SubmitAnswer:
    assert isinstance(state.turn, ExpansionQuestion)
    return SubmitAnswer(player, state.turn.deadline.id, NumericAnswer(Decimal(value)), elapsed)


def test_first_answer_only_records_it() -> None:
    state = started()
    events = decide(state, answer(state, P1, 100, 500), DecisionContext(now=NOW))
    assert [type(e) for e in events] == [ev.AnswerSubmitted]


def test_repeating_the_same_answer_is_ignored() -> None:
    state = started()
    state = fold(state, decide(state, answer(state, P1, 100, 500), DecisionContext(now=NOW)))
    assert decide(state, answer(state, P1, 100, 500), DecisionContext(now=NOW)) == ()


def test_changing_the_answer_is_rejected() -> None:
    state = started()
    state = fold(state, decide(state, answer(state, P1, 100, 500), DecisionContext(now=NOW)))
    with pytest.raises(RejectedCommand) as exc:
        decide(state, answer(state, P1, 999, 600), DecisionContext(now=NOW))
    assert exc.value.code is RejectCode.ALREADY_ANSWERED


def test_wrong_answer_kind_is_rejected() -> None:
    state = started()
    assert isinstance(state.turn, ExpansionQuestion)
    command = SubmitAnswer(P1, state.turn.deadline.id, ChoiceAnswer(0), 100)
    with pytest.raises(RejectedCommand) as exc:
        decide(state, command, DecisionContext(now=NOW))
    assert exc.value.code is RejectCode.ANSWER_KIND_MISMATCH


def test_last_answer_closes_and_resolves_the_window() -> None:
    state = started()
    for player, guess, elapsed in ((P1, 100, 500), (P2, 90, 400)):
        state = fold(
            state, decide(state, answer(state, player, guess, elapsed), DecisionContext(now=NOW))
        )
    events = decide(state, answer(state, P3, 105, 300), DecisionContext(now=NOW))
    assert [type(e) for e in events] == [
        ev.AnswerSubmitted,
        ev.AnswerWindowClosed,
        ev.QuestionResolved,
        ev.PicksGranted,
    ]


def test_ranking_is_by_distance_then_speed() -> None:
    state = started()  # correct answer for "numeric 0?" is 100
    for player, guess, elapsed in ((P1, 105, 900), (P2, 95, 200), (P3, 95, 100)):
        events = decide(state, answer(state, player, guess, elapsed), DecisionContext(now=NOW))
        state = fold(state, events)
    resolved = next(e for e in events if isinstance(e, ev.QuestionResolved))
    # p3 and p2 are both 5 away; p3 was faster. p1 is 5 away too but slowest.
    assert resolved.ranking == (P3, P2, P1)


def test_non_answerers_rank_last_by_seat() -> None:
    state = started()
    state = fold(state, decide(state, answer(state, P3, 100, 100), DecisionContext(now=NOW)))
    expired = ExpireDeadline(state.turn.deadline.id)  # type: ignore[union-attr]
    late = DecisionContext(now=state.turn.deadline.deadline_at + timedelta(seconds=1))  # type: ignore[union-attr]
    events = decide(state, expired, late)
    resolved = next(e for e in events if isinstance(e, ev.QuestionResolved))
    assert resolved.ranking == (P3, P1, P2)


def test_grants_follow_claims_by_rank_and_open_picking() -> None:
    state = started()
    for player, guess, elapsed in ((P1, 100, 100), (P2, 110, 100), (P3, 120, 100)):
        events = decide(state, answer(state, player, guess, elapsed), DecisionContext(now=NOW))
        state = fold(state, events)
    granted = next(e for e in events if isinstance(e, ev.PicksGranted))
    assert granted.grants == {P1: 2, P2: 1, P3: 0}
    assert granted.pick_order == (P1, P2)
    assert isinstance(state.turn, ExpansionPicking)
    assert state.turn.current_picker == P1


def test_when_every_ranked_player_has_a_zero_claim_no_picking_window_opens() -> None:
    """`claims_by_rank` grants by rank position among the currently *active*
    players, not by seat. If the only nonzero rank is one that elimination
    has pushed out of range, nobody gets picks and the round moves straight
    on — `order` comes back empty even though free regions remain."""
    rules = replace(DEFAULT_RULES, claims_by_rank=(0, 0, 3))
    base = lobby_state(rules=rules)
    state = fold(base, decide(base, StartGame(P1), start_ctx()))
    state = fold(state, decide(state, Surrender(P3), DecisionContext(now=NOW)))
    state = expire_warmup(state)
    assert isinstance(state.turn, ExpansionQuestion)
    assert state.active_players() == (P1, P2)

    state = fold(state, decide(state, answer(state, P1, 100, 500), DecisionContext(now=NOW)))
    events = decide(state, answer(state, P2, 110, 500), DecisionContext(now=NOW))
    assert not any(isinstance(e, ev.PicksGranted) for e in events)
    assert any(isinstance(e, ev.ExpansionRoundCompleted) for e in events)

    after = fold(state, events)
    assert after.round_no == 2
    assert isinstance(after.turn, ExpansionQuestion)


def test_grants_are_truncated_to_free_regions() -> None:
    state = started()
    # 9 regions, 3 taken by bases, leave only 1 free by handing 5 to p1.
    from tests.conftest import own

    for region in ("r1", "r3", "r4", "r5", "r7"):
        state = own(state, region, "p1")
    for player, guess, elapsed in ((P1, 100, 100), (P2, 110, 100), (P3, 120, 100)):
        events = decide(state, answer(state, player, guess, elapsed), DecisionContext(now=NOW))
        state = fold(state, events)
    granted = next(e for e in events if isinstance(e, ev.PicksGranted))
    assert sum(granted.grants.values()) == 1
