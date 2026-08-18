"""§8.7 and §12.3: the pre-resolution DTO does not *contain* the answer.

Not "sets it to None", not "excludes it on dump". The field is absent from
the model, so no serialization flag, no future `model_dump(exclude_none=
False)`, and no debug endpoint can put it back.
"""

from decimal import Decimal

from tests.conftest import lobby_state, mc_question, numeric_question
from triviador.api.projection.viewer import viewer_for
from triviador.api.schemas.games import ClientQuestion, RevealedAnswer, project_question
from triviador.domain.ids import MediaAssetId, PlayerId, SessionId, UserId
from triviador.services.identity import AuthenticatedPrincipal, UserRole

FORBIDDEN_FIELDS = {
    "is_correct",
    "correct",
    "correct_index",
    "correct_choice_index",
    "correct_choice_id",
    "correct_value",
    "numeric_answer",
    "answer",
}


def test_the_question_model_declares_no_answer_field_anywhere() -> None:
    """Walks the models, not an instance: a field that only appears on a
    numeric question would pass an instance check made against an MC
    fixture, and vice versa."""
    from triviador.api.schemas.games import ClientChoice

    names = set(ClientQuestion.model_fields) | set(ClientChoice.model_fields)
    assert not (names & FORBIDDEN_FIELDS), sorted(names & FORBIDDEN_FIELDS)


def test_a_multiple_choice_question_projects_its_text_and_never_its_key() -> None:
    projected = project_question(mc_question(1, correct=2), media_base="/media")
    assert [c.text for c in projected.choices or ()] == ["a", "b", "c", "d"]
    assert "correct" not in projected.model_dump_json()


def test_a_numeric_question_projects_without_its_value() -> None:
    projected = project_question(numeric_question(1, answer=42), media_base="/media")
    assert projected.choices is None
    assert "42" not in projected.model_dump_json()


def test_the_revealed_answer_is_a_separate_type_that_does_carry_it() -> None:
    """The withholding is structural, so revealing must be too: a different
    model, constructed only by `QuestionResolved`'s projection (Task 12)."""
    revealed = RevealedAnswer.of(numeric_question(1, answer=42))
    assert revealed.correct_value == Decimal(42)
    assert RevealedAnswer.of(mc_question(1, correct=2)).correct_choice_index == 2


def test_media_is_an_opaque_content_addressed_url() -> None:
    """§9.6: prefetching ~29 of these must leak neither prompt nor answer,
    which is exactly what a content-addressed id gives — and why the URL is
    built from the asset id alone, never from the question id or its text."""
    from dataclasses import replace

    question = replace(numeric_question(1, answer=42), media_asset_id=MediaAssetId("a3f9c1"))
    projected = project_question(question, media_base="/media")
    assert projected.media_url == "/media/a3f9c1"


def test_a_question_without_media_has_no_url() -> None:
    assert project_question(numeric_question(1, 42), media_base="/media").media_url is None


def test_the_viewer_is_a_participant_only_when_the_state_says_so() -> None:
    state = lobby_state({"p1": 0, "p2": 1})
    inside = viewer_for(
        state, AuthenticatedPrincipal(UserId("p1"), UserRole.PLAYER, SessionId("s"))
    )
    outside = viewer_for(
        state, AuthenticatedPrincipal(UserId("p9"), UserRole.PLAYER, SessionId("s"))
    )
    assert inside.player_id == PlayerId("p1")
    assert outside.player_id is None


def test_an_admin_who_is_not_playing_is_still_not_a_participant() -> None:
    """Role and participation are different questions. Spec 2 adds
    spectating; until then an admin's standing in a game they are not in is
    the same as anyone else's."""
    state = lobby_state({"p1": 0})
    viewer = viewer_for(
        state, AuthenticatedPrincipal(UserId("admin"), UserRole.ADMIN, SessionId("s"))
    )
    assert viewer.player_id is None
    assert viewer.role is UserRole.ADMIN
