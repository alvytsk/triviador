from decimal import Decimal

import pytest

from triviador.domain.ids import CategoryId, QuestionId
from triviador.domain.questions.types import (
    CategorySnapshot,
    ChoiceSnapshot,
    Difficulty,
    QuestionBudget,
    QuestionKind,
    QuestionPool,
    QuestionSnapshot,
)

CATEGORY = CategorySnapshot(CategoryId("c1"), "history", "History")


def a_numeric(n: int) -> QuestionSnapshot:
    return QuestionSnapshot(
        question_id=QuestionId(f"n{n}"),
        version=1,
        kind=QuestionKind.NUMERIC,
        prompt=f"numeric {n}?",
        category=CATEGORY,
        difficulty=Difficulty.MEDIUM,
        choices=None,
        numeric_answer=Decimal(n),
        unit="year",
        media_asset_id=None,
    )


def a_mc(n: int) -> QuestionSnapshot:
    return QuestionSnapshot(
        question_id=QuestionId(f"m{n}"),
        version=1,
        kind=QuestionKind.MULTIPLE_CHOICE,
        prompt=f"mc {n}?",
        category=CATEGORY,
        difficulty=Difficulty.EASY,
        choices=(
            ChoiceSnapshot(0, "a", is_correct=True, media_asset_id=None),
            ChoiceSnapshot(1, "b", is_correct=False, media_asset_id=None),
            ChoiceSnapshot(2, "c", is_correct=False, media_asset_id=None),
            ChoiceSnapshot(3, "d", is_correct=False, media_asset_id=None),
        ),
        numeric_answer=None,
        unit=None,
        media_asset_id=None,
    )


def test_drawing_is_sequential_and_returns_a_new_pool() -> None:
    pool = QuestionPool(numeric=(a_numeric(1), a_numeric(2)), multiple_choice=(a_mc(1),))

    first, pool2 = pool.next_numeric()
    second, pool3 = pool2.next_numeric()

    assert first.question_id == QuestionId("n1")
    assert second.question_id == QuestionId("n2")
    assert pool.numeric_used == 0, "drawing must not mutate the original pool"
    assert pool3.numeric_used == 2


def test_drawing_past_the_end_raises() -> None:
    pool = QuestionPool(numeric=(a_numeric(1),), multiple_choice=())
    _, exhausted = pool.next_numeric()
    with pytest.raises(IndexError):
        exhausted.next_numeric()


def test_covers_compares_against_a_budget() -> None:
    pool = QuestionPool(numeric=(a_numeric(1), a_numeric(2)), multiple_choice=(a_mc(1),))
    assert pool.covers(QuestionBudget(numeric=2, multiple_choice=1)) is True
    assert pool.covers(QuestionBudget(numeric=3, multiple_choice=1)) is False


def test_correct_choice_index_is_derived() -> None:
    assert a_mc(1).correct_choice_index() == 0


def test_correct_choice_index_raises_when_no_choice_is_correct() -> None:
    mc_with_no_correct = QuestionSnapshot(
        question_id=QuestionId("m_bad"),
        version=1,
        kind=QuestionKind.MULTIPLE_CHOICE,
        prompt="mc bad?",
        category=CATEGORY,
        difficulty=Difficulty.EASY,
        choices=(
            ChoiceSnapshot(0, "a", is_correct=False, media_asset_id=None),
            ChoiceSnapshot(1, "b", is_correct=False, media_asset_id=None),
            ChoiceSnapshot(2, "c", is_correct=False, media_asset_id=None),
            ChoiceSnapshot(3, "d", is_correct=False, media_asset_id=None),
        ),
        numeric_answer=None,
        unit=None,
        media_asset_id=None,
    )
    with pytest.raises(ValueError, match="has no correct choice"):
        mc_with_no_correct.correct_choice_index()
