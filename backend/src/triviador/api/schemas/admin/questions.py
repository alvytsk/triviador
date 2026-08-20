from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict

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
