from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from triviador.domain.questions.types import Difficulty, QuestionKind


class ChoiceView(BaseModel):
    model_config = ConfigDict(extra="forbid")

    idx: int
    text: str
    is_correct: bool
    media_asset_id: str | None


class QuestionSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    kind: QuestionKind
    prompt: str
    category_id: str
    category_slug: str
    difficulty: Difficulty
    is_active: bool
    has_media: bool
    version: int
    updated_at: datetime


class QuestionDetail(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    kind: QuestionKind
    prompt: str
    category_id: str
    category_slug: str
    difficulty: Difficulty
    is_active: bool
    version: int
    media_asset_id: str | None
    choices: list[ChoiceView] | None
    numeric_answer: Decimal | None
    unit: str | None


class QuestionPageView(BaseModel):
    """`total` is the unpaged count, so the client can render "page 3 of
    17" without a second request — the one thing offset pagination is
    actually good at."""

    model_config = ConfigDict(extra="forbid")

    items: list[QuestionSummary]
    total: int
    limit: int
    offset: int


class ChoiceWrite(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str = Field(min_length=1, max_length=200)
    is_correct: bool


class QuestionWriteRequest(BaseModel):
    """Both kinds in one body, validated as a whole.

    A discriminated union of two request models would be tidier on paper
    and worse here: the editor is one form whose fields appear and
    disappear, and a client posting `{kind: "numeric", choices: []}`
    deserves the field-level error this shape gives it rather than "no
    variant matched".
    """

    model_config = ConfigDict(extra="forbid")

    kind: QuestionKind
    prompt: str = Field(min_length=1, max_length=1000)
    category_id: str
    difficulty: Difficulty
    media_asset_id: str | None = None
    choices: list[ChoiceWrite] | None = None
    numeric_answer: Decimal | None = None
    unit: str | None = Field(default=None, max_length=16)

    @model_validator(mode="after")
    def _shape(self) -> "QuestionWriteRequest":
        if self.kind is QuestionKind.MULTIPLE_CHOICE:
            if self.choices is None or len(self.choices) != 4:
                raise ValueError("a multiple-choice question needs exactly four choices")
            if sum(1 for c in self.choices if c.is_correct) != 1:
                raise ValueError("a multiple-choice question needs exactly one correct choice")
            if self.numeric_answer is not None or self.unit is not None:
                raise ValueError("a multiple-choice question carries no numeric answer")
        else:
            if self.numeric_answer is None:
                raise ValueError("a numeric question needs an answer")
            if not self.numeric_answer.is_finite():
                raise ValueError("a numeric answer must be finite")
            if self.choices:
                raise ValueError("a numeric question carries no choices")
        return self


class QuestionSaved(BaseModel):
    """The saved question, plus §10.2's duplicate *warning*.

    One response rather than a 409 and a retry: the admin has already
    written the question, and the only useful thing to do with the
    similarity is show it beside what they saved.
    """

    model_config = ConfigDict(extra="forbid")

    question: QuestionDetail
    duplicate_of: list[str]
