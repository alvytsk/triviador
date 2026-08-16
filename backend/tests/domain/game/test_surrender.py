from dataclasses import replace
from decimal import Decimal

from tests.conftest import expire_warmup, full_pool, lobby_state
from tests.domain.game.test_duel import dueling
from tests.domain.game.test_start import start_ctx
from tests.domain.game.test_target_select import CTX, P1, P2, P3, battle_state, open_turn
from triviador.domain.game import events as ev
from triviador.domain.game.actions import AbortGame, StartGame, SubmitAnswer, Surrender
from triviador.domain.game.reducer import _next_battle_turn, decide, fold
from triviador.domain.game.rules import DEFAULT_RULES
from triviador.domain.game.state import (
    AcquisitionKind,
    BattleTargetSelect,
    ExpansionQuestion,
    FinalTiebreak,
    GameState,
    NumericAnswer,
    Phase,
    TerritoryKind,
)
from triviador.domain.ids import PlayerId, RegionId


def with_p1_base() -> object:
    state = battle_state()
    base = replace(
        state.territories[RegionId("r0")],
        kind=TerritoryKind.BASE,
        base_owner_id=P1,
        base_hp=3,
        acquisition=AcquisitionKind.BASE,
    )
    state = replace(state, territories={**state.territories, RegionId("r0"): base})
    p1 = state.players[P1]
    from triviador.domain.game.scoring import expected_score

    state = replace(
        state,
        players={
            **state.players,
            P1: replace(p1, base_region=RegionId("r0"), score=expected_score(state, P1)),
        },
    )
    return open_turn(state)


def test_surrender_in_the_lobby_is_just_leaving() -> None:
    state = lobby_state()
    assert decide(state, Surrender(P1), CTX) == (ev.PlayerLeft(P1),)


def test_folding_a_lobby_surrender_removes_the_player() -> None:
    """Regression: `_apply` had no arm for `PlayerLeft`, so replaying (or
    even just applying) the event this exact command produces crashed with
    `NotImplementedError: no evolve branch for PlayerLeft` — `decide` was
    fine, `fold` was not, and nothing in the matrix (Task 20) caught it
    because the matrix only ever calls `decide`."""
    state = lobby_state()
    events = decide(state, Surrender(P1), CTX)
    after = fold(state, events)
    assert P1 not in after.players
    assert P1 not in after.turn_order
    assert set(after.players) == {P2, P3}


def test_the_current_attacker_surrendering_aborts_the_turn_and_advances() -> None:
    """Regression: `_next_battle_turn` used to rotate on the pre-filtered
    `active` list. The surrendering attacker is eliminated (and so dropped
    from `active`) before rotation runs, so `last not in active` made the
    old code jump straight to "round over" — completing round 1 early and
    starting round 2, whose first attacker is *also* P2. That coincidence
    let this test pass while silently cutting P2 and P3's round-1 turns.
    Asserting no `BattleRoundCompleted` (and that we're still in round 1)
    pins the real fix, not the accidental one."""
    state = with_p1_base()
    events = decide(state, Surrender(P1), CTX)  # type: ignore[arg-type]
    kinds = [type(e) for e in events]
    assert kinds[0] is ev.PlayerSurrendered
    assert ev.PlayerEliminated in kinds
    assert ev.TerritoryNeutralized in kinds
    assert ev.TurnAborted in kinds
    assert ev.BattleRoundCompleted not in kinds
    assert kinds.index(ev.TurnAborted) < kinds.index(ev.TurnStarted)
    after = fold(state, events)  # type: ignore[arg-type]
    assert after.round_no == 1
    assert isinstance(after.turn, BattleTargetSelect)
    assert after.turn.attacker_id == P2


def test_the_final_round_attacker_surrendering_still_gives_the_rest_their_turns() -> None:
    """Regression: with the `active`-anchored rotation, a player out of
    contention could surrender at the start of the LAST battle round to
    immediately complete it and finish the game, denying every other
    player their round-4 turn (and freezing standings in their favour)."""
    state = battle_state()
    state = replace(state, round_no=state.rules.battle_rounds)
    state = open_turn(state)
    events = decide(state, Surrender(P1), CTX)
    kinds = [type(e) for e in events]
    assert ev.GameFinished not in kinds
    assert ev.BattleRoundCompleted not in kinds
    assert ev.TurnStarted in kinds
    after = fold(state, events)
    assert after.phase is not Phase.FINISHED
    assert after.round_no == state.rules.battle_rounds
    assert isinstance(after.turn, BattleTargetSelect)
    assert after.turn.attacker_id == P2


def test_a_surrendering_players_own_base_is_neutralized_not_awarded() -> None:
    state = with_p1_base()
    after = fold(state, decide(state, Surrender(P1), CTX))  # type: ignore[arg-type]
    base = after.territories[RegionId("r0")]
    assert base.owner_id is None
    assert base.acquisition is None
    assert after.owned_by(P1) == ()


def test_a_duel_defender_surrendering_discards_the_question() -> None:
    state = dueling()  # p1 attacking p2's r2
    events = decide(state, Surrender(P2), CTX)
    assert not any(isinstance(e, ev.DuelResolved | ev.TerritoryCaptured) for e in events)
    assert any(isinstance(e, ev.TurnAborted) for e in events)


def test_surrender_keeps_accumulated_bonuses() -> None:
    state = with_p1_base()
    p1 = state.players[P1]  # type: ignore[attr-defined]
    state = replace(  # type: ignore[type-var]
        state,
        players={
            **state.players,  # type: ignore[attr-defined]
            P1: replace(p1, bonus_score=300, score=p1.score + 300),
        },
    )
    after = fold(state, decide(state, Surrender(P1), CTX))  # type: ignore[arg-type]
    assert after.players[P1].bonus_score == 300
    assert after.players[P1].score == 300


def test_surrender_leaving_one_active_player_finishes_the_game() -> None:
    state = battle_state()
    p3 = state.players[P3]
    state = replace(state, players={**state.players, P3: replace(p3, is_eliminated=True)})
    state = open_turn(state)
    events = decide(state, Surrender(P1), CTX)
    assert any(isinstance(e, ev.GameFinished) for e in events)
    after = fold(state, events)
    assert after.phase is Phase.FINISHED
    assert after.winner_id == P2


def test_surrender_during_a_final_tiebreak_is_ignored() -> None:
    """HUMAN RULING: a contender cannot eliminate themselves mid-tiebreak by
    surrendering. `Surrender` stays legal in `LEGAL_COMMANDS[FinalTiebreak]`
    (Task 10), but `_decide_surrender` must silently drop it — no events, no
    elimination, no `TurnAborted` — rather than reject it outright."""
    state = battle_state()
    tied = {P1: 500, P2: 500, P3: 100}
    state = replace(
        state,
        players={p: replace(s, score=tied[p]) for p, s in state.players.items()},
        round_no=state.rules.battle_rounds,
    )
    state = fold(state, _next_battle_turn(state, CTX))
    assert isinstance(state.turn, FinalTiebreak)
    assert decide(state, Surrender(P1), CTX) == ()
    assert fold(state, decide(state, Surrender(P1), CTX)) == state


def test_surrender_during_warmup_finishes_a_two_player_game() -> None:
    """Spec 1 §3.6: one active player remaining ends the game — including
    before the first question has ever been presented (spec §3.4)."""
    two = replace(DEFAULT_RULES, player_count=2, claims_by_rank=(2, 1))
    state = lobby_state(players={"p1": 0, "p2": 1}, rules=two)
    ctx = replace(
        start_ctx(),
        shuffled_player_ids=(P1, P2),
        base_regions=(RegionId("r0"), RegionId("r2")),
        drawn_pool=full_pool(),
    )
    state = fold(state, decide(state, StartGame(P1), ctx))

    events = decide(state, Surrender(P2), ctx)

    assert any(isinstance(e, ev.GameFinished) for e in events)
    after = fold(state, events)
    assert after.phase is Phase.FINISHED
    assert after.winner_id == P1


def test_a_surrendered_players_answer_does_not_close_the_window() -> None:
    """Spec 1 §3.3: every *active* player answers or times out. The window used
    to close as soon as `len(answers) >= len(active_players())`, and a
    surrendered player's answer stays in `answers` while they leave `active` —
    so two counts that should both shrink moved toward each other instead."""
    state = fold(lobby_state(), decide(lobby_state(), StartGame(P1), start_ctx()))
    state = expire_warmup(state)
    assert isinstance(state.turn, ExpansionQuestion)
    window = state.turn.deadline.id

    def answer(s: GameState, who: str, guess: int) -> GameState:
        cmd = SubmitAnswer(PlayerId(who), window, NumericAnswer(Decimal(guess)), 100)
        return fold(s, decide(s, cmd, CTX))

    state = answer(state, "p1", 100)
    state = fold(state, decide(state, Surrender(P1), CTX))
    state = answer(state, "p2", 110)

    assert isinstance(state.turn, ExpansionQuestion), (
        "p3 has neither answered nor timed out — the window must still be open"
    )


def test_abort_works_from_any_non_terminal_phase() -> None:
    for state in (lobby_state(), open_turn(battle_state())):
        events = decide(state, AbortGame(P1), CTX)
        assert [type(e) for e in events] == [ev.GameAborted]
        after = fold(state, events)
        assert after.phase is Phase.ABORTED
        assert after.winner_id is None
        assert after.turn is None
