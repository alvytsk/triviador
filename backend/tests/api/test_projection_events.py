"""§8.7's table, and §8.4's reason a batch is the transport unit.

`project_event` may return `None`. That is not an oversight — it is why the
client is sequenced on the whole committed batch (`base_seq`/`seq`) rather
than per event: a client that saw 101 and 103 would conclude there was a
gap, resync, and repeat forever.
"""

from decimal import Decimal
from typing import get_args

import pytest

from tests.conftest import mc_question, numeric_question
from triviador.api.projection.events import project_event
from triviador.api.projection.viewer import ViewerContext
from triviador.api.schemas.events import (
    PlayerAnsweredEvent,
    QuestionPresentedEvent,
    QuestionResolvedEvent,
)
from triviador.domain.game import events as ev
from triviador.domain.game.events import GameEvent
from triviador.domain.game.rules import DEFAULT_RULES
from triviador.domain.game.state import Deadline, DeadlineKind, NumericAnswer, SubmittedAnswer
from triviador.domain.ids import DeadlineId, MapId, PlayerId, UserId
from triviador.domain.questions.types import QuestionPool
from triviador.services.identity import UserRole


def viewer(pid: str | None = "p1") -> ViewerContext:
    return ViewerContext(UserId(pid or "x"), PlayerId(pid) if pid else None, UserRole.PLAYER)


def test_the_drawn_pool_never_reaches_a_client() -> None:
    """The single most dangerous event in the log: it carries the entire
    match's questions and their correct answers."""
    pool = QuestionPool(numeric=(numeric_question(1, 42),), multiple_choice=(mc_question(1),))
    assert project_event(ev.QuestionPoolDrawn(pool), viewer()) is None


def test_genesis_never_reaches_a_client() -> None:
    """`GameCreated` is consumed by `create_initial_state` and never folded,
    so it is never in a published batch; returning `None` makes that
    explicit rather than relying on it."""
    created = ev.GameCreated(MapId("grid"), DEFAULT_RULES, PlayerId("p1"), "sha")
    assert project_event(created, viewer()) is None


def test_an_answer_is_the_fact_to_everyone_else() -> None:
    """§8.7's middle row, the whole reason `publish` takes domain objects:
    one event, two different client events, decided per subscriber."""
    event = ev.AnswerSubmitted(PlayerId("p2"), SubmittedAnswer(NumericAnswer(Decimal(99)), 1200))
    projected = project_event(event, viewer("p1"))
    assert isinstance(projected, PlayerAnsweredEvent)
    assert projected.player_id == "p2"
    assert "99" not in projected.model_dump_json()


def test_an_answer_is_its_value_to_its_author() -> None:
    event = ev.AnswerSubmitted(PlayerId("p1"), SubmittedAnswer(NumericAnswer(Decimal(99)), 1200))
    projected = project_event(event, viewer("p1"))
    assert isinstance(projected, PlayerAnsweredEvent)
    assert projected.your_answer is not None
    assert projected.your_answer.value == "99"


def test_the_elapsed_time_is_never_published() -> None:
    """It is the tiebreak key. Publishing it live would let a player time
    their own submission against an opponent's already-known speed."""
    event = ev.AnswerSubmitted(PlayerId("p1"), SubmittedAnswer(NumericAnswer(Decimal(99)), 1200))
    projected = project_event(event, viewer("p1"))
    assert projected is not None and "1200" not in projected.model_dump_json()


def test_resolution_reveals_everything_to_everyone() -> None:
    """§8.7's bottom row: after `QuestionResolved` the answer is public, and
    it is the same for a participant and its author."""
    event = ev.QuestionResolved(
        correct_choice_index=None,
        correct_value=Decimal(42),
        ranking=(PlayerId("p1"), PlayerId("p2")),
        correct_players=(PlayerId("p1"),),
    )
    for who in ("p1", "p2", None):
        projected = project_event(event, viewer(who))
        assert isinstance(projected, QuestionResolvedEvent)
        assert projected.correct_value == "42"
        assert projected.ranking == ("p1", "p2")


def test_a_presented_question_is_announced_without_being_repeated() -> None:
    """The question itself is in the snapshot's turn. Projecting it here as
    well would be a second place the withholding has to be right, and the
    second place is the one that gets it wrong."""
    deadline = Deadline(
        DeadlineId(4), DeadlineKind.ANSWER, __import__("tests.conftest", fromlist=["NOW"]).NOW
    )
    projected = project_event(ev.QuestionPresented(numeric_question(1, 42), deadline), viewer())
    assert isinstance(projected, QuestionPresentedEvent)
    assert projected.deadline_id == 4
    assert "42" not in projected.model_dump_json()
    assert "numeric 1?" not in projected.model_dump_json()


def test_every_domain_event_has_an_explicit_decision() -> None:
    """No event may fall through to a default. The failure this prevents is
    silent: a new event type added in a later spec would otherwise project
    as `None`, and the feature would simply never appear in any client with
    nothing anywhere reporting it."""
    from triviador.api.projection import events as module

    decided = module.PROJECTED | module.WITHHELD
    assert {t.__name__ for t in get_args(GameEvent)} == decided


@pytest.mark.parametrize("event_type", [t.__name__ for t in get_args(GameEvent)])
def test_no_projected_event_shares_a_base_class_with_its_domain_event(event_type: str) -> None:
    """§8.7: `DomainEvent` and `ServerMessage` are separate types with no
    shared base class, so `send_json(event.model_dump())` cannot compile."""
    from triviador.api.schemas import events as schemas

    for model in vars(schemas).values():
        if isinstance(model, type) and model.__module__ == schemas.__name__:
            assert not any(
                base.__module__.startswith("triviador.domain") for base in model.__mro__[1:]
            )
