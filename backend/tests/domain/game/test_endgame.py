from dataclasses import replace
from decimal import Decimal

import pytest

from tests.domain.game.test_target_select import CTX, P1, P2, P3, battle_state, open_turn
from triviador.domain.game import events as ev
from triviador.domain.game.actions import (
    DecisionContext,
    ExpireDeadline,
    RejectCode,
    RejectedCommand,
    SubmitAnswer,
)
from triviador.domain.game.reducer import _finish, _next_battle_turn, decide, fold
from triviador.domain.game.state import (
    BattleTargetSelect,
    FinalTiebreak,
    GameState,
    NumericAnswer,
    Phase,
)


def _expired(state: GameState) -> DecisionContext:
    """A context timed exactly at the open turn's deadline.

    A single fixed "later" timestamp cannot be reused across a sequence of
    `ExpireDeadline` calls: each hop schedules its next deadline as
    `ctx.now + timeout`, which is unconditionally later than `ctx.now` itself,
    so a constant clock is always "early" (guard 4) by the second call. Anchor
    every expiry to the deadline actually being expired instead.
    """
    assert state.turn is not None
    return DecisionContext(now=state.turn.deadline.deadline_at)


def skip_turn(state: GameState) -> GameState:
    """Let the current attacker time out without acting."""
    assert isinstance(state.turn, BattleTargetSelect)
    return fold(state, decide(state, ExpireDeadline(state.turn.deadline.id), _expired(state)))


def test_turns_cycle_through_active_players_in_turn_order() -> None:
    state = open_turn(battle_state())
    seen = []
    for _ in range(3):
        assert isinstance(state.turn, BattleTargetSelect)
        seen.append(state.turn.attacker_id)
        state = skip_turn(state)
    assert seen == [P1, P2, P3]


def test_eliminated_players_are_skipped_as_attackers() -> None:
    state = battle_state()
    p2 = state.players[P2]
    state = replace(state, players={**state.players, P2: replace(p2, is_eliminated=True)})
    state = open_turn(state)
    assert isinstance(state.turn, BattleTargetSelect)
    assert state.turn.attacker_id == P1
    state = skip_turn(state)
    assert isinstance(state.turn, BattleTargetSelect)
    assert state.turn.attacker_id == P3


def test_the_last_attacker_of_a_round_starts_the_next_round() -> None:
    state = open_turn(battle_state())
    for _ in range(2):
        state = skip_turn(state)
    assert isinstance(state.turn, BattleTargetSelect)
    events = decide(state, ExpireDeadline(state.turn.deadline.id), _expired(state))
    kinds = [type(e) for e in events]
    assert ev.BattleRoundCompleted in kinds
    assert ev.BattleRoundStarted in kinds
    assert fold(state, events).round_no == 2


def test_exhausting_battle_rounds_finishes_the_game() -> None:
    # battle_state()'s default layout ties p1 and p3 at 600 each, which would
    # open a FinalTiebreak instead of finishing outright — that path belongs
    # to the final-tiebreak tests below, not here, so break the tie.
    state = battle_state()
    boosted = replace(state.players[P1], score=state.players[P1].score + 1)
    state = replace(
        state, players={**state.players, P1: boosted}, rules=replace(state.rules, battle_rounds=1)
    )
    state = open_turn(state)
    for _ in range(2):
        state = skip_turn(state)
    assert isinstance(state.turn, BattleTargetSelect)
    events = decide(state, ExpireDeadline(state.turn.deadline.id), _expired(state))
    assert any(isinstance(e, ev.GameFinished) for e in events)
    after = fold(state, events)
    assert after.phase is Phase.FINISHED


def test_the_highest_scorer_wins_outright() -> None:
    state = battle_state()
    boosted = replace(state.players[P3], score=99_999)
    state = replace(
        state, players={**state.players, P3: boosted}, rules=replace(state.rules, battle_rounds=1)
    )
    state = open_turn(state)
    for _ in range(2):
        state = skip_turn(state)
    assert isinstance(state.turn, BattleTargetSelect)
    events = decide(state, ExpireDeadline(state.turn.deadline.id), _expired(state))
    finished = next(e for e in events if isinstance(e, ev.GameFinished))
    assert finished.winner_id == P3


def test_finish_with_no_active_players_declares_no_winner() -> None:
    """`_finish` is keyed off `state.players`, not `active_players()`
    (rotation bookkeeping), so a state where every player has been
    eliminated must resolve to `GameFinished(None, ...)` rather than
    crashing on an empty `max()`."""
    state = battle_state()
    state = replace(
        state,
        players={pid: replace(p, is_eliminated=True) for pid, p in state.players.items()},
    )
    events = _finish(state, CTX)
    assert events == (ev.GameFinished(None, {p: s.score for p, s in state.players.items()}),)


def _tied_final_tiebreak() -> GameState:
    state = battle_state()
    tied = {P1: 500, P2: 500, P3: 100}
    state = replace(
        state,
        players={p: replace(s, score=tied[p]) for p, s in state.players.items()},
        round_no=state.rules.battle_rounds,
    )
    return fold(state, _next_battle_turn(state, CTX))


def test_a_non_contender_cannot_answer_the_final_tiebreak() -> None:
    state = _tied_final_tiebreak()
    assert isinstance(state.turn, FinalTiebreak)
    assert P3 not in state.turn.contenders
    bystander = SubmitAnswer(P3, state.turn.deadline.id, NumericAnswer(Decimal(0)), 100)
    with pytest.raises(RejectedCommand) as exc:
        decide(state, bystander, CTX)
    assert exc.value.code is RejectCode.NOT_YOUR_TURN


def test_repeating_the_same_final_tiebreak_answer_is_ignored() -> None:
    state = _tied_final_tiebreak()
    assert isinstance(state.turn, FinalTiebreak)
    cmd = SubmitAnswer(P1, state.turn.deadline.id, NumericAnswer(Decimal(123)), 300)
    state = fold(state, decide(state, cmd, CTX))
    assert isinstance(state.turn, FinalTiebreak)  # still waiting on the other contender
    assert decide(state, cmd, CTX) == ()


def test_a_score_tie_opens_a_final_tiebreak_among_the_tied_only() -> None:
    state = battle_state()
    tied = {P1: 500, P2: 500, P3: 100}
    state = replace(state, players={p: replace(s, score=tied[p]) for p, s in state.players.items()})
    events = _next_battle_turn(
        replace(state, round_no=state.rules.battle_rounds, turn_order=(), players=state.players),
        CTX,
    )
    started = next(e for e in events if isinstance(e, ev.FinalTiebreakStarted))
    assert set(started.contenders) == {P1, P2}


def test_the_tiebreak_winner_becomes_the_winner() -> None:
    state = battle_state()
    tied = {P1: 500, P2: 500, P3: 100}
    state = replace(
        state,
        pool=state.pool,
        players={p: replace(s, score=tied[p]) for p, s in state.players.items()},
        round_no=state.rules.battle_rounds,
    )
    state = fold(state, _next_battle_turn(state, CTX))
    assert isinstance(state.turn, FinalTiebreak)
    correct = int(state.turn.question.numeric_answer)  # type: ignore[arg-type]
    window = state.turn.deadline.id
    state = fold(
        state,
        decide(state, SubmitAnswer(P1, window, NumericAnswer(Decimal(correct + 100)), 300), CTX),
    )
    events = decide(state, SubmitAnswer(P2, window, NumericAnswer(Decimal(correct)), 300), CTX)
    finished = next(e for e in events if isinstance(e, ev.GameFinished))
    assert finished.winner_id == P2
    after = fold(state, events)
    assert after.phase is Phase.FINISHED
    assert after.winner_id == P2
