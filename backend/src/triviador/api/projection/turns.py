"""`Turn` → `ClientTurn`, with the viewer's own affordances attached.

Every option list here is *read from the domain*, never recomputed:
`state.free_regions()` and `legal_targets(state, player)` are the same
functions the reducer's guards call. That is the whole property §8.8 buys —
the client highlights exactly what the server would accept, and adjacency
lives in `domain/maps` alone.
"""

from typing import assert_never

from triviador.api.projection.viewer import ViewerContext
from triviador.api.schemas.games import (
    ClientTurn,
    DuelTurn,
    FinalTurn,
    NeutralTurn,
    PickingTurn,
    QuestionTurn,
    SubmittedValue,
    TargetSelectTurn,
    WarmupTurn,
    YourOptions,
    project_question,
)
from triviador.domain.game.reducer import legal_targets
from triviador.domain.game.state import (
    BattleDuel,
    BattleTargetSelect,
    BattleTiebreak,
    ChoiceAnswer,
    ExpansionPicking,
    ExpansionQuestion,
    FinalTiebreak,
    GameState,
    MediaWarmup,
    NeutralChallenge,
    SubmittedAnswer,
    Turn,
)


def _answered(turn: object) -> tuple[str, ...]:
    answers = getattr(turn, "answers", {})
    return tuple(str(p) for p in answers)


def _own_answer(turn: Turn, viewer: ViewerContext) -> SubmittedValue | None:
    answers = getattr(turn, "answers", {})
    if viewer.player_id is None:
        return None
    submitted: SubmittedAnswer | None = answers.get(viewer.player_id)
    if submitted is None:
        return None
    if isinstance(submitted.value, ChoiceAnswer):
        return SubmittedValue(kind="choice", idx=submitted.value.idx)
    return SubmittedValue(kind="numeric", value=str(submitted.value.value))


def project_turn(state: GameState, viewer: ViewerContext, *, media_base: str) -> ClientTurn | None:
    turn = state.turn
    if turn is None:
        return None

    deadline_id = int(turn.deadline.id)
    deadline_at = turn.deadline.deadline_at
    me = viewer.player_id

    match turn:
        case MediaWarmup():
            return WarmupTurn(deadline_id=deadline_id, deadline_at=deadline_at)
        case ExpansionQuestion():
            return QuestionTurn(
                deadline_id=deadline_id,
                deadline_at=deadline_at,
                question=project_question(turn.question, media_base=media_base),
                answered=_answered(turn),
                your_answer=_own_answer(turn, viewer),
            )
        case ExpansionPicking():
            options = (
                YourOptions(pick=tuple(str(r) for r in state.free_regions()))
                if me is not None and me == turn.current_picker
                else YourOptions()
            )
            return PickingTurn(
                deadline_id=deadline_id,
                deadline_at=deadline_at,
                your_options=options,
                pick_order=tuple(str(p) for p in turn.pick_order),
                grants_remaining={str(p): n for p, n in turn.grants_remaining.items()},
                current_picker=str(turn.current_picker),
            )
        case BattleTargetSelect():
            options = (
                YourOptions(attack=tuple(str(r) for r in legal_targets(state, me)))
                if me is not None and me == turn.attacker_id
                else YourOptions()
            )
            return TargetSelectTurn(
                deadline_id=deadline_id,
                deadline_at=deadline_at,
                your_options=options,
                attacker_id=str(turn.attacker_id),
            )
        case BattleDuel() | BattleTiebreak():
            return DuelTurn(
                deadline_id=deadline_id,
                deadline_at=deadline_at,
                tiebreak=isinstance(turn, BattleTiebreak),
                attacker_id=str(turn.attacker_id),
                defender_id=str(turn.defender_id),
                region_id=str(turn.region_id),
                question=project_question(turn.question, media_base=media_base),
                answered=_answered(turn),
                your_answer=_own_answer(turn, viewer),
            )
        case NeutralChallenge():
            return NeutralTurn(
                deadline_id=deadline_id,
                deadline_at=deadline_at,
                attacker_id=str(turn.attacker_id),
                region_id=str(turn.region_id),
                question=project_question(turn.question, media_base=media_base),
                answered=_answered(turn),
                your_answer=_own_answer(turn, viewer),
            )
        case FinalTiebreak():
            return FinalTurn(
                deadline_id=deadline_id,
                deadline_at=deadline_at,
                contenders=tuple(str(p) for p in turn.contenders),
                question=project_question(turn.question, media_base=media_base),
                answered=_answered(turn),
                your_answer=_own_answer(turn, viewer),
            )
        case _:
            assert_never(turn)
