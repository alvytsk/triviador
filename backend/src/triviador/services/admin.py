"""What the admin surface asks of the database, as Protocols.

Same rule as `ports.py` and `identity.py`: `api/` depends on these, `db/`
implements them, neither imports the other, and `tests/api/` runs the
whole admin surface against in-memory fakes with no PostgreSQL.

One port per resource rather than one `AdminPort` with thirty methods:
`tests/api/fakes.py` has to implement whatever a route touches, and a
single wide port would make every fake grow a method for every route in
the plan.
"""

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Protocol


@dataclass(frozen=True)
class MediaAssetRecord:
    asset_id: str
    mime_type: str
    width: int | None
    height: int | None
    byte_size: int
    storage_key: str


class MediaAssetPort(Protocol):
    async def ensure(
        self,
        *,
        asset_id: str,
        mime_type: str,
        width: int,
        height: int,
        byte_size: int,
        storage_key: str,
        created_by: str,
    ) -> tuple[MediaAssetRecord, bool]:
        """The record, and whether this call created it.

        Two admins uploading the same image produce the same sha256 and so
        the same row; the boolean is what lets the route answer 201 the
        first time and 200 afterwards rather than raising on a primary-key
        collision that means "this already worked".
        """
        ...

    async def get(self, asset_id: str) -> MediaAssetRecord | None: ...


@dataclass(frozen=True)
class QuestionFilters:
    """§10.2's filter set. Every field is `None` for "do not filter",
    which is why `is_active` is `bool | None` and not `bool`: the admin
    list defaults to *everything*, and a `False` default would hide the
    active bank behind a filter nobody set."""

    kind: str | None = None
    category_id: str | None = None
    difficulty: str | None = None
    is_active: bool | None = None
    has_media: bool | None = None
    search: str | None = None


@dataclass(frozen=True)
class ChoiceRecord:
    idx: int
    text: str
    is_correct: bool
    media_asset_id: str | None


@dataclass(frozen=True)
class QuestionSummaryRecord:
    question_id: str
    kind: str
    prompt: str
    category_id: str
    category_slug: str
    difficulty: str
    is_active: bool
    has_media: bool
    version: int
    updated_at: datetime


@dataclass(frozen=True)
class QuestionDetailRecord:
    question_id: str
    kind: str
    prompt: str
    category_id: str
    category_slug: str
    difficulty: str
    is_active: bool
    version: int
    media_asset_id: str | None
    choices: tuple[ChoiceRecord, ...] | None
    numeric_answer: Decimal | None
    unit: str | None


@dataclass(frozen=True)
class QuestionPage:
    items: tuple[QuestionSummaryRecord, ...]
    total: int


class QuestionAdminPort(Protocol):
    async def list(
        self, filters: QuestionFilters, *, limit: int, offset: int
    ) -> QuestionPage: ...
    async def get(self, question_id: str) -> QuestionDetailRecord | None: ...
