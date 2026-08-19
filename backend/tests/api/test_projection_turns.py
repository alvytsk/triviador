"""§8.8: the projection carries affordances, not just facts.

The client greys out illegal moves by highlighting exactly `your_options`.
It does not derive them — deriving them means shipping adjacency and
ownership rules to the browser, i.e. a second copy of the ruleset that can
disagree with `domain/maps`.
"""

from datetime import timedelta

from tests.conftest import NOW, full_pool, lobby_state, own
from triviador.api.projection.turns import project_turn
from triviador.api.projection.viewer import ViewerContext
from triviador.domain.game.reducer import legal_targets
from triviador.domain.game.state import (
    BattleDuel,
    BattleTargetSelect,
    Deadline,
    DeadlineKind,
    ExpansionPicking,
    ExpansionQuestion,
    GameState,
    MediaWarmup,
    Phase,
)
from triviador.domain.ids import DeadlineId, PlayerId, RegionId, UserId
from triviador.services.identity import UserRole

MEDIA = "/media"


def viewer(pid: str | None) -> ViewerContext:
    return ViewerContext(UserId(pid or "watcher"), PlayerId(pid) if pid else None, UserRole.PLAYER)


def deadline(kind: DeadlineKind) -> Deadline:
    return Deadline(DeadlineId(7), kind, NOW + timedelta(seconds=20))


def test_no_turn_projects_to_none() -> None:
    assert project_turn(lobby_state(), viewer("p1"), media_base=MEDIA) is None


def test_the_warmup_turn_carries_only_its_deadline() -> None:
    from dataclasses import replace

    state = replace(lobby_state(), turn=MediaWarmup(deadline(DeadlineKind.WARMUP)))
    turn = project_turn(state, viewer("p1"), media_base=MEDIA)
    assert turn is not None and turn.kind == "media_warmup"
    assert turn.deadline_id == 7
    assert turn.your_options.pick == () and turn.your_options.attack == ()


def question_state() -> GameState:
    from dataclasses import replace

    return replace(
        lobby_state(),
        phase=Phase.EXPANSION,
        pool=full_pool(),
        turn=ExpansionQuestion(
            deadline=deadline(DeadlineKind.ANSWER),
            question=full_pool().numeric[0],
            answers={},
        ),
    )


def test_a_question_turn_names_who_has_answered_and_never_what_they_said() -> None:
    """§8.7's middle row: to a participant, `AnswerSubmitted` is the fact,
    not the value. The snapshot has to say the same thing, or a reconnect
    reveals what the live stream withheld."""
    from dataclasses import replace
    from decimal import Decimal

    from triviador.domain.game.state import NumericAnswer, SubmittedAnswer

    base = question_state()
    assert isinstance(base.turn, ExpansionQuestion)
    state = replace(
        base,
        turn=replace(
            base.turn,
            answers={PlayerId("p2"): SubmittedAnswer(NumericAnswer(Decimal(99)), 1200)},
        ),
    )
    turn = project_turn(state, viewer("p1"), media_base=MEDIA)
    assert turn is not None and turn.kind == "expansion_question"
    assert turn.answered == ("p2",)
    assert turn.your_answer is None
    assert "99" not in turn.model_dump_json()


def test_an_author_sees_their_own_answer_back() -> None:
    """§8.7's right-hand column. Without it a reconnect mid-window loses
    what the player already typed, and they retype it into a window that
    will reject the change as ALREADY_ANSWERED."""
    from dataclasses import replace
    from decimal import Decimal

    from triviador.domain.game.state import NumericAnswer, SubmittedAnswer

    base = question_state()
    assert isinstance(base.turn, ExpansionQuestion)
    state = replace(
        base,
        turn=replace(
            base.turn,
            answers={PlayerId("p1"): SubmittedAnswer(NumericAnswer(Decimal(99)), 1200)},
        ),
    )
    turn = project_turn(state, viewer("p1"), media_base=MEDIA)
    assert turn is not None and turn.kind == "expansion_question"
    assert turn.your_answer is not None
    assert turn.your_answer.value == "99"


def duel_state() -> GameState:
    from dataclasses import replace

    state = own(own(lobby_state(), "r0", "p1"), "r4", "p2")
    pool = full_pool(numeric=0, mc=1)
    return replace(
        state,
        phase=Phase.BATTLE,
        pool=pool,
        turn=BattleDuel(
            deadline=deadline(DeadlineKind.ANSWER),
            attacker_id=PlayerId("p1"),
            defender_id=PlayerId("p2"),
            region_id=RegionId("r4"),
            question=pool.multiple_choice[0],
            answers={},
        ),
    )


def test_an_author_sees_their_own_choice_answer_back() -> None:
    """`_own_answer`'s other branch: `ChoiceAnswer` -> `SubmittedValue(kind="choice", ...)`.
    A `BattleDuel` presents multiple-choice questions, so it is the natural home."""
    from dataclasses import replace

    from triviador.domain.game.state import ChoiceAnswer, SubmittedAnswer

    base = duel_state()
    assert isinstance(base.turn, BattleDuel)
    state = replace(
        base,
        turn=replace(
            base.turn,
            answers={PlayerId("p1"): SubmittedAnswer(ChoiceAnswer(2), 900)},
        ),
    )
    turn = project_turn(state, viewer("p1"), media_base=MEDIA)
    assert turn is not None and turn.kind == "battle_duel"
    assert turn.your_answer is not None
    assert turn.your_answer.kind == "choice"
    assert turn.your_answer.idx == 2


def test_a_non_author_does_not_see_a_choice_answer() -> None:
    """The split proven for numeric answers above must hold for choice
    answers too: a spectator or opponent gets nothing back."""
    from dataclasses import replace

    from triviador.domain.game.state import ChoiceAnswer, SubmittedAnswer

    base = duel_state()
    assert isinstance(base.turn, BattleDuel)
    state = replace(
        base,
        turn=replace(
            base.turn,
            answers={PlayerId("p1"): SubmittedAnswer(ChoiceAnswer(2), 900)},
        ),
    )
    turn = project_turn(state, viewer("p2"), media_base=MEDIA)
    assert turn is not None and turn.kind == "battle_duel"
    assert turn.your_answer is None


def picking_state(current: str) -> GameState:
    from dataclasses import replace

    state = own(lobby_state(), "r0", "p1")
    return replace(
        state,
        phase=Phase.EXPANSION,
        turn=ExpansionPicking(
            deadline=deadline(DeadlineKind.PICK),
            pick_order=(PlayerId("p1"), PlayerId("p2"), PlayerId("p3")),
            grants_remaining={PlayerId("p1"): 2, PlayerId("p2"): 1, PlayerId("p3"): 0},
            current_picker=PlayerId(current),
        ),
    )


def test_the_current_picker_is_offered_exactly_the_free_regions() -> None:
    state = picking_state("p1")
    turn = project_turn(state, viewer("p1"), media_base=MEDIA)
    assert turn is not None and turn.kind == "expansion_picking"
    assert set(turn.your_options.pick) == set(state.free_regions())
    assert RegionId("r0") not in turn.your_options.pick


def test_a_player_who_is_not_picking_is_offered_nothing() -> None:
    """The affordance is per viewer. A shared list would let the client
    render a legal-looking move for the wrong player, and the server would
    then reject it — which reads as a bug in the game, not in the client."""
    turn = project_turn(picking_state("p2"), viewer("p1"), media_base=MEDIA)
    assert turn is not None and turn.your_options.pick == ()


def test_a_non_participant_is_offered_nothing() -> None:
    turn = project_turn(picking_state("p1"), viewer(None), media_base=MEDIA)
    assert turn is not None and turn.your_options.pick == ()


def target_state() -> GameState:
    from dataclasses import replace

    state = own(own(lobby_state(), "r0", "p1"), "r4", "p2")
    return replace(
        state,
        phase=Phase.BATTLE,
        turn=BattleTargetSelect(
            deadline=deadline(DeadlineKind.TARGET_SELECT), attacker_id=PlayerId("p1")
        ),
    )


def test_the_attacker_is_offered_exactly_legal_targets() -> None:
    """The one source of the adjacency rule is `legal_targets`, which the
    reducer's own guard 6 also calls. Recomputing the set here — even
    correctly — would be a second copy that can drift."""
    state = target_state()
    turn = project_turn(state, viewer("p1"), media_base=MEDIA)
    assert turn is not None and turn.kind == "battle_target_select"
    assert set(turn.your_options.attack) == set(legal_targets(state, PlayerId("p1")))
    assert turn.your_options.pick == ()


def test_the_defender_is_offered_nothing_during_a_target_selection() -> None:
    turn = project_turn(target_state(), viewer("p2"), media_base=MEDIA)
    assert turn is not None and turn.your_options.attack == ()


def test_every_turn_variant_has_a_projection() -> None:
    """An unmapped `Turn` variant must be a `mypy --strict` error at the
    `assert_never`, not a `None` the client renders as an empty board. This
    test is the runtime half: it enumerates the union and asserts each name
    appears in the projected `kind` literal set."""
    from typing import get_args

    from triviador.api.schemas.games import ClientTurn
    from triviador.domain.game.state import Turn

    # `ClientTurn` is `Annotated[Union[...], Field(discriminator=...)]`, so
    # `get_args` first strips the `Annotated` wrapper (returning the union
    # plus its metadata) before the union's own members can be enumerated.
    union = get_args(ClientTurn)[0]
    kinds = {get_args(v.model_fields["kind"].annotation)[0] for v in get_args(union)}
    assert len(kinds) == len(get_args(Turn)) - 1  # BattleTiebreak shares DuelTurn's shape
