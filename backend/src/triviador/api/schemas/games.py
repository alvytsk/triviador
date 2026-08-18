"""The player-facing shapes. Nothing here shares a base class with a
domain event or a domain state (§8.7) — these are Pydantic models over
plain JSON types, and the domain is frozen dataclasses over `Decimal`,
`NewType` and `Mapping`. The gap is deliberate: it is what makes
`send_json(event.model_dump())` fail to typecheck.
"""

from datetime import datetime
from decimal import Decimal
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

from triviador.domain.questions.types import Difficulty, QuestionKind, QuestionSnapshot


def media_url(media_base: str, asset_id: str | None) -> str | None:
    return None if asset_id is None else f"{media_base}/{asset_id}"


class ClientChoice(BaseModel):
    """No `is_correct`. §12.3 rejects byte-scanning as the test for this,
    because the correct answer's *text* is legitimate content — so the
    guarantee has to be that the flag has nowhere to live."""

    model_config = ConfigDict(extra="forbid")

    idx: int
    text: str
    media_url: str | None = None


class ClientQuestion(BaseModel):
    model_config = ConfigDict(extra="forbid")

    question_id: str
    kind: QuestionKind
    prompt: str
    category: str
    difficulty: Difficulty
    choices: tuple[ClientChoice, ...] | None
    unit: str | None
    media_url: str | None


class RevealedAnswer(BaseModel):
    """The other half, constructed only by `QuestionResolved`'s projection.

    A separate model rather than optional fields on `ClientQuestion`: an
    optional field is one `exclude_none=False` away from being emitted, and
    the whole point is that before resolution there is no field at all.
    """

    model_config = ConfigDict(extra="forbid")

    correct_choice_index: int | None
    correct_value: Decimal | None

    @classmethod
    def of(cls, question: QuestionSnapshot) -> "RevealedAnswer":
        return cls(
            correct_choice_index=(
                question.correct_choice_index() if question.choices is not None else None
            ),
            correct_value=question.numeric_answer,
        )


def project_question(question: QuestionSnapshot, *, media_base: str) -> ClientQuestion:
    return ClientQuestion(
        question_id=str(question.question_id),
        kind=question.kind,
        prompt=question.prompt,
        category=question.category.name,
        difficulty=question.difficulty,
        choices=(
            None
            if question.choices is None
            else tuple(
                ClientChoice(
                    idx=c.idx,
                    text=c.text,
                    media_url=media_url(media_base, c.media_asset_id),
                )
                for c in question.choices
            )
        ),
        unit=question.unit,
        media_url=media_url(media_base, question.media_asset_id),
    )


class SubmittedValue(BaseModel):
    """A player's own answer, echoed back to its author only.

    `value` is a string even for a numeric answer: JSON has one number type
    and it is a float, so `Decimal("0.1")` round-trips through it wrong.
    Every numeric value on this API is a decimal string for that reason.
    """

    model_config = ConfigDict(extra="forbid")

    kind: Literal["choice", "numeric"]
    idx: int | None = None
    value: str | None = None


class YourOptions(BaseModel):
    """§8.8's `your_options`, per viewer. Both lists empty is the normal
    case — it is not this viewer's move."""

    model_config = ConfigDict(extra="forbid")

    pick: tuple[str, ...] = ()
    attack: tuple[str, ...] = ()


class _TurnBase(BaseModel):
    model_config = ConfigDict(extra="forbid")

    deadline_id: int
    deadline_at: datetime
    your_options: YourOptions = YourOptions()


class WarmupTurn(_TurnBase):
    kind: Literal["media_warmup"] = "media_warmup"


class QuestionTurn(_TurnBase):
    kind: Literal["expansion_question"] = "expansion_question"
    question: ClientQuestion
    answered: tuple[str, ...]
    your_answer: SubmittedValue | None = None


class PickingTurn(_TurnBase):
    kind: Literal["expansion_picking"] = "expansion_picking"
    pick_order: tuple[str, ...]
    grants_remaining: dict[str, int]
    current_picker: str


class TargetSelectTurn(_TurnBase):
    kind: Literal["battle_target_select"] = "battle_target_select"
    attacker_id: str


class DuelTurn(_TurnBase):
    """`BattleDuel` and `BattleTiebreak` share this shape; `tiebreak`
    distinguishes them. Two models would be two identical field lists and
    two Zod schemas for one screen."""

    kind: Literal["battle_duel"] = "battle_duel"
    tiebreak: bool
    attacker_id: str
    defender_id: str
    region_id: str
    question: ClientQuestion
    answered: tuple[str, ...]
    your_answer: SubmittedValue | None = None


class NeutralTurn(_TurnBase):
    kind: Literal["neutral_challenge"] = "neutral_challenge"
    attacker_id: str
    region_id: str
    question: ClientQuestion
    answered: tuple[str, ...]
    your_answer: SubmittedValue | None = None


class FinalTurn(_TurnBase):
    kind: Literal["final_tiebreak"] = "final_tiebreak"
    contenders: tuple[str, ...]
    question: ClientQuestion
    answered: tuple[str, ...]
    your_answer: SubmittedValue | None = None


ClientTurn = Annotated[
    WarmupTurn | QuestionTurn | PickingTurn | TargetSelectTurn | DuelTurn | NeutralTurn | FinalTurn,
    Field(discriminator="kind"),
]
