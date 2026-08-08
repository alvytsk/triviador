from dataclasses import dataclass, replace
from decimal import Decimal
from enum import StrEnum

from triviador.domain.ids import CategoryId, MediaAssetId, QuestionId


class QuestionKind(StrEnum):
    MULTIPLE_CHOICE = "multiple_choice"
    NUMERIC = "numeric"


class Difficulty(StrEnum):
    EASY = "easy"
    MEDIUM = "medium"
    HARD = "hard"


@dataclass(frozen=True)
class CategorySnapshot:
    category_id: CategoryId
    slug: str
    name: str


@dataclass(frozen=True)
class ChoiceSnapshot:
    idx: int
    text: str
    is_correct: bool
    media_asset_id: MediaAssetId | None


@dataclass(frozen=True)
class QuestionSnapshot:
    """A question frozen at pool-draw time.

    Once this exists inside the event log, the game never reads the question
    bank again — an admin editing or deactivating the source row cannot change
    a game in flight or corrupt replay.
    """

    question_id: QuestionId
    version: int
    kind: QuestionKind
    prompt: str
    category: CategorySnapshot
    difficulty: Difficulty
    choices: tuple[ChoiceSnapshot, ...] | None
    numeric_answer: Decimal | None
    unit: str | None
    media_asset_id: MediaAssetId | None

    def correct_choice_index(self) -> int:
        if self.choices is None:
            raise ValueError(f"question {self.question_id!r} has no choices")
        return next(c.idx for c in self.choices if c.is_correct)


@dataclass(frozen=True)
class QuestionBudget:
    numeric: int
    multiple_choice: int


@dataclass(frozen=True)
class QuestionPool:
    numeric: tuple[QuestionSnapshot, ...]
    multiple_choice: tuple[QuestionSnapshot, ...]
    numeric_used: int = 0
    mc_used: int = 0

    def covers(self, budget: QuestionBudget) -> bool:
        return (
            len(self.numeric) >= budget.numeric
            and len(self.multiple_choice) >= budget.multiple_choice
        )

    def next_numeric(self) -> tuple[QuestionSnapshot, "QuestionPool"]:
        if self.numeric_used >= len(self.numeric):
            raise IndexError("numeric question pool exhausted")
        question = self.numeric[self.numeric_used]
        return question, replace(self, numeric_used=self.numeric_used + 1)

    def next_multiple_choice(self) -> tuple[QuestionSnapshot, "QuestionPool"]:
        if self.mc_used >= len(self.multiple_choice):
            raise IndexError("multiple-choice question pool exhausted")
        question = self.multiple_choice[self.mc_used]
        return question, replace(self, mc_used=self.mc_used + 1)
