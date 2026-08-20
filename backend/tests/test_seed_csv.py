"""The seed file's format, without a database in sight."""

from decimal import Decimal

import pytest

from triviador.cli import parse_seed_csv
from triviador.db.repositories.questions import prompt_digest
from triviador.domain.questions.types import Difficulty, QuestionKind

HEADER = (
    "kind,category_slug,category_name,difficulty,prompt,unit,answer,"
    "choice_1,choice_2,choice_3,choice_4,correct_index\n"
)
NUMERIC = 'numeric,science,Science,easy,"How hot is it?",°C,100,,,,,\n'
CHOICE = (
    'multiple_choice,science,Science,easy,"Which one?",,,Right,Wrong,Also wrong,Still wrong,0\n'
)


def test_a_numeric_row_parses() -> None:
    (question,) = parse_seed_csv(HEADER + NUMERIC)
    assert question.kind is QuestionKind.NUMERIC
    assert question.difficulty is Difficulty.EASY
    assert question.correct_value == Decimal("100")
    assert question.unit == "°C"
    assert question.choices == ()
    assert question.correct_index is None


def test_a_choice_row_parses_and_keeps_exactly_one_correct_answer() -> None:
    (question,) = parse_seed_csv(HEADER + CHOICE)
    assert question.kind is QuestionKind.MULTIPLE_CHOICE
    assert sorted(question.choices) == sorted(["Right", "Wrong", "Also wrong", "Still wrong"])
    assert question.correct_index is not None
    assert question.choices[question.correct_index] == "Right"
    assert question.correct_value is None
    assert question.unit is None


def test_choices_are_shuffled_away_from_the_authored_order() -> None:
    """Every row in the shipped file authors the correct answer first. If
    the parser preserved that, the game would be trivially winnable."""
    prompts = [f'multiple_choice,c,C,easy,"Question {i}?",,,Right,B,C,D,0' for i in range(30)]
    questions = parse_seed_csv(HEADER + "\n".join(prompts) + "\n")
    first_is_correct = [q.correct_index == 0 for q in questions]
    assert sum(first_is_correct) < len(questions)


def test_the_shuffle_is_stable_for_the_same_prompt() -> None:
    """Re-running the seed must be a no-op, which it cannot be if the same
    question comes out with its answers in a different order each time."""
    once = parse_seed_csv(HEADER + CHOICE)[0]
    twice = parse_seed_csv(HEADER + CHOICE)[0]
    assert once.choices == twice.choices
    assert once.correct_index == twice.correct_index


@pytest.mark.parametrize(
    "row",
    [
        'numeric,s,S,easy,"No answer",,,,,,,',
        'numeric,s,S,easy,"Not a number",,abc,,,,,',
        'multiple_choice,s,S,easy,"Only two",,,A,B,,,0',
        'multiple_choice,s,S,easy,"Index out of range",,,A,B,C,D,9',
        'multiple_choice,s,S,easy,"Numeric fields set",u,5,A,B,C,D,0',
        'sideways,s,S,easy,"Unknown kind",,1,,,,,',
        'numeric,s,S,tepid,"Unknown difficulty",,1,,,,,',
    ],
)
def test_a_malformed_row_names_its_line(row: str) -> None:
    with pytest.raises(ValueError, match="line 2"):
        parse_seed_csv(HEADER + row + "\n")


def test_a_duplicate_prompt_in_one_file_is_refused() -> None:
    with pytest.raises(ValueError, match="duplicate"):
        parse_seed_csv(HEADER + NUMERIC + NUMERIC)


def test_prompt_digest_ignores_whitespace_and_case() -> None:
    assert prompt_digest("How  hot\nis it?") == prompt_digest("how hot is it?")


def test_the_shipped_seed_file_meets_the_default_preset_budget() -> None:
    """Spec 1 §14.3, as a test rather than a promise. `required_question_budget`
    of the default rules is numeric=17, multiple_choice=12."""
    from pathlib import Path

    path = Path(__file__).resolve().parents[2] / "data" / "seeds" / "questions.csv"
    questions = parse_seed_csv(path.read_text(encoding="utf-8"))
    numeric = [q for q in questions if q.kind is QuestionKind.NUMERIC]
    choice = [q for q in questions if q.kind is QuestionKind.MULTIPLE_CHOICE]
    assert len(numeric) >= 17
    assert len(choice) >= 12
