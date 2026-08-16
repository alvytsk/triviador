from dataclasses import replace

import pytest

from tests.conftest import NOW, full_pool, lobby_state
from triviador.domain.game import events as ev
from triviador.domain.game.actions import (
    DecisionContext,
    JoinGame,
    RejectCode,
    RejectedCommand,
    StartGame,
)
from triviador.domain.game.reducer import decide, fold
from triviador.domain.game.state import ExpansionQuestion, Phase, TerritoryKind
from triviador.domain.ids import PlayerId, RegionId
from triviador.domain.questions.types import QuestionPool

P1, P2, P3 = PlayerId("p1"), PlayerId("p2"), PlayerId("p3")
BASES = (RegionId("r0"), RegionId("r2"), RegionId("r6"))


def start_ctx() -> DecisionContext:
    return DecisionContext(
        now=NOW, shuffled_player_ids=(P1, P2, P3), base_regions=BASES, drawn_pool=full_pool()
    )


def test_joining_an_empty_lobby_emits_player_joined() -> None:
    state = lobby_state(players={})
    events = decide(state, JoinGame(P1, "One"), DecisionContext(now=NOW))
    assert events == (ev.PlayerJoined(P1, "One", seat=0),)


def test_folding_player_joined_adds_the_player_to_the_state() -> None:
    state = lobby_state(players={})
    events = decide(state, JoinGame(P1, "One"), DecisionContext(now=NOW))
    after = fold(state, events)
    assert after.players[P1].display_name == "One"
    assert after.players[P1].seat == 0
    assert after.turn_order == (P1,)


def test_joining_twice_is_rejected() -> None:
    state = lobby_state(players={"p1": 0})
    with pytest.raises(RejectedCommand) as exc:
        decide(state, JoinGame(P1, "One"), DecisionContext(now=NOW))
    assert exc.value.code is RejectCode.ALREADY_JOINED


def test_joining_a_full_lobby_is_rejected() -> None:
    state = lobby_state()  # 3 players, player_count is 3
    with pytest.raises(RejectedCommand) as exc:
        decide(state, JoinGame(PlayerId("p4"), "Four"), DecisionContext(now=NOW))
    assert exc.value.code is RejectCode.GAME_FULL


def test_starting_short_handed_is_rejected() -> None:
    state = lobby_state(players={"p1": 0, "p2": 1})
    with pytest.raises(RejectedCommand) as exc:
        decide(state, StartGame(P1), start_ctx())
    assert exc.value.code is RejectCode.NOT_ENOUGH_PLAYERS


def test_starting_without_enough_questions_is_rejected() -> None:
    ctx = replace(start_ctx(), drawn_pool=QuestionPool(numeric=(), multiple_choice=()))
    with pytest.raises(RejectedCommand) as exc:
        decide(lobby_state(), StartGame(P1), ctx)
    assert exc.value.code is RejectCode.QUESTION_POOL_INSUFFICIENT


def test_starting_with_a_base_count_mismatch_is_rejected() -> None:
    """The runtime is trusted to shuffle players and draw bases, but `decide`
    still validates the shapes line up before committing to them."""
    ctx = replace(start_ctx(), base_regions=BASES[:2])
    with pytest.raises(RejectedCommand) as exc:
        decide(lobby_state(), StartGame(P1), ctx)
    assert exc.value.code is RejectCode.WRONG_TURN_STATE


def test_starting_with_an_unknown_player_id_is_rejected() -> None:
    """A malformed `shuffled_player_ids` (an id not in the lobby) used to
    escape as a raw `KeyError` from `zip`/`state.players[...]` lookups
    downstream instead of a `RejectedCommand` at the boundary."""
    ctx = replace(start_ctx(), shuffled_player_ids=(P1, P2, PlayerId("ghost")))
    with pytest.raises(RejectedCommand) as exc:
        decide(lobby_state(), StartGame(P1), ctx)
    assert exc.value.code is RejectCode.WRONG_TURN_STATE


def test_starting_with_a_duplicate_base_region_is_rejected() -> None:
    """A malformed `base_regions` with a duplicate used to be accepted
    structurally (`len(bases) == len(order)` still holds), silently giving
    one player no base at all and breaking the `score == holdings + bonus`
    invariant from the very first tick."""
    ctx = replace(start_ctx(), base_regions=(RegionId("r0"), RegionId("r0"), RegionId("r6")))
    with pytest.raises(RejectedCommand) as exc:
        decide(lobby_state(), StartGame(P1), ctx)
    assert exc.value.code is RejectCode.WRONG_TURN_STATE


def test_starting_with_a_base_region_not_on_the_map_is_rejected() -> None:
    ctx = replace(start_ctx(), base_regions=(RegionId("r0"), RegionId("r2"), RegionId("nope")))
    with pytest.raises(RejectedCommand) as exc:
        decide(lobby_state(), StartGame(P1), ctx)
    assert exc.value.code is RejectCode.WRONG_TURN_STATE


def test_start_emits_the_full_opening_sequence() -> None:
    events = decide(lobby_state(), StartGame(P1), start_ctx())
    kinds = [type(e) for e in events]
    assert kinds == [
        ev.GameStarted,
        ev.BasesAssigned,
        ev.ScoreChanged,
        ev.ScoreChanged,
        ev.ScoreChanged,
        ev.QuestionPoolDrawn,
        ev.ExpansionRoundStarted,
        ev.QuestionPresented,
    ]


def test_start_records_the_pool_as_snapshots_not_ids() -> None:
    events = decide(lobby_state(), StartGame(P1), start_ctx())
    drawn = next(e for e in events if isinstance(e, ev.QuestionPoolDrawn))
    assert drawn.pool.numeric[0].prompt == "numeric 0?"


def test_after_start_bases_are_owned_and_scored() -> None:
    state = fold(lobby_state(), decide(lobby_state(), StartGame(P1), start_ctx()))
    assert state.phase is Phase.EXPANSION
    assert state.round_no == 1
    for player, region in zip((P1, P2, P3), BASES, strict=True):
        territory = state.territories[region]
        assert territory.owner_id == player
        assert territory.kind is TerritoryKind.BASE
        assert territory.base_hp == state.rules.base_hp
        assert state.players[player].score == state.rules.pts_base
        assert state.players[player].base_region == region


def test_after_start_an_expansion_question_window_is_open() -> None:
    state = fold(lobby_state(), decide(lobby_state(), StartGame(P1), start_ctx()))
    assert isinstance(state.turn, ExpansionQuestion)
    assert state.turn.question.prompt == "numeric 0?"
    assert state.turn.deadline.deadline_at > NOW
    assert state.pool.numeric_used == 1
