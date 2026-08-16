"""Fixtures for the transition matrix: one state per turn variant, one command
per column. Commands are always built against the *current* window so guard 2
never masks the cell under test (the lobby row's `join` and `start` cells are
the deliberate exception — see test_matrix.py)."""

from collections.abc import Callable
from dataclasses import replace
from decimal import Decimal

import pytest

from tests.conftest import NOW, expire_warmup, full_pool, lobby_state
from tests.domain.game.test_duel import dueling, mc
from tests.domain.game.test_expansion_picking import picking_state
from tests.domain.game.test_neutral import challenging
from tests.domain.game.test_start import P1, P2, P3, start_ctx
from tests.domain.game.test_target_select import battle_state, open_turn
from triviador.domain.game.actions import (
    AbortGame,
    Command,
    DecisionContext,
    ExpireDeadline,
    JoinGame,
    PickRegion,
    SelectAttackTarget,
    StartGame,
    SubmitAnswer,
    Surrender,
)
from triviador.domain.game.reducer import _next_battle_turn, decide, fold
from triviador.domain.game.state import (
    BattleDuel,
    BattleTargetSelect,
    BattleTiebreak,
    ChoiceAnswer,
    ExpansionPicking,
    ExpansionQuestion,
    FinalTiebreak,
    GameState,
    NeutralChallenge,
    NumericAnswer,
    Phase,
)
from triviador.domain.ids import DeadlineId, PlayerId, RegionId
from triviador.domain.questions.types import QuestionKind

CommandBuilder = Callable[[GameState], Command]

# Turn variants whose `.question` attribute drives whether an answer must be a
# NumericAnswer or a ChoiceAnswer. Turns not in this tuple (ExpansionPicking,
# BattleTargetSelect, or no turn at all) never actually reach an answer's
# domain logic in the matrix, so any value is a safe default there.
_HAS_QUESTION = (ExpansionQuestion, BattleDuel, BattleTiebreak, NeutralChallenge, FinalTiebreak)


class States(dict[str, GameState]):
    ctx: DecisionContext = DecisionContext(now=NOW)


def _media_warmup() -> GameState:
    base = lobby_state()
    return fold(base, decide(base, StartGame(P1), start_ctx()))


def _expansion_question() -> GameState:
    """StartGame now opens a warmup window; the first question is one expiry
    later."""
    return expire_warmup(_media_warmup())


def _battle_tiebreak() -> GameState:
    state = dueling()
    state = fold(state, decide(state, mc(state, P1, 0), States.ctx))
    return fold(state, decide(state, mc(state, P2, 0), States.ctx))


def _final_tiebreak() -> GameState:
    state = battle_state()
    tied = {P1: 500, P2: 500, P3: 100}
    state = replace(
        state,
        players={p: replace(s, score=tied[p]) for p, s in state.players.items()},
        round_no=state.rules.battle_rounds,
        pool=full_pool(),
    )
    return fold(state, _next_battle_turn(state, States.ctx))


@pytest.fixture
def states() -> States:
    out = States()
    out["lobby"] = lobby_state()
    out["media_warmup"] = _media_warmup()
    out["expansion_question"] = _expansion_question()
    out["expansion_picking"] = picking_state()
    out["battle_target"] = open_turn(battle_state())
    out["battle_duel"] = dueling()
    out["battle_tiebreak"] = _battle_tiebreak()
    out["neutral_challenge"] = challenging()
    out["final_tiebreak"] = _final_tiebreak()
    out["finished"] = replace(lobby_state(), phase=Phase.FINISHED, turn=None)
    out["aborted"] = replace(lobby_state(), phase=Phase.ABORTED, turn=None)
    return out


def _actor(state: GameState) -> PlayerId:
    """Whoever is legitimately expected to act right now."""
    turn = state.turn
    if isinstance(turn, ExpansionPicking):
        return turn.current_picker
    if isinstance(turn, BattleTargetSelect | BattleDuel | BattleTiebreak | NeutralChallenge):
        return turn.attacker_id
    active = state.active_players()
    return active[0] if active else P1


def _window(state: GameState) -> DeadlineId:
    """The open window, or a sentinel when there is none (terminal/lobby rows)."""
    deadline = state.current_deadline()
    return deadline.id if deadline is not None else DeadlineId(0)


def _free_or_any(state: GameState) -> RegionId:
    free = state.free_regions()
    return free[0] if free else RegionId("r4")


def _is_numeric(state: GameState) -> bool:
    turn = state.turn
    if not isinstance(turn, _HAS_QUESTION):
        return True
    return turn.question.kind is QuestionKind.NUMERIC


@pytest.fixture
def commands() -> dict[str, CommandBuilder]:
    return {
        "join": lambda s: JoinGame(PlayerId("newcomer"), "New"),
        "start": lambda s: StartGame(_actor(s)),
        "answer": lambda s: SubmitAnswer(
            _actor(s),
            _window(s),
            NumericAnswer(Decimal(100)) if _is_numeric(s) else ChoiceAnswer(0),
            300,
        ),
        "pick": lambda s: PickRegion(_actor(s), _window(s), _free_or_any(s)),
        "target": lambda s: SelectAttackTarget(_actor(s), _window(s), RegionId("r4")),
        "expire": lambda s: ExpireDeadline(_window(s)),
        "surrender": lambda s: Surrender(_actor(s)),
        "abort": lambda s: AbortGame(_actor(s)),
    }
