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


@dataclass(frozen=True)
class QuestionWrite:
    """One question as an admin submits it, in either kind.

    Four choices, exactly one correct, is fixed rather than configurable —
    Spec 1 §10.2: "a configurable count buys nothing and costs variability
    in the answer grid". The tuple carries `(text, is_correct)` pairs in
    display order; `idx` is the position, not a field an admin sets.
    """

    kind: str
    prompt: str
    category_id: str
    difficulty: str
    media_asset_id: str | None
    choices: tuple[tuple[str, bool], ...] | None
    numeric_answer: Decimal | None
    unit: str | None


class QuestionAdminPort(Protocol):
    async def list(
        self, filters: QuestionFilters, *, limit: int, offset: int
    ) -> QuestionPage: ...
    async def get(self, question_id: str) -> QuestionDetailRecord | None: ...
    async def create(self, write: QuestionWrite) -> QuestionDetailRecord:
        """No `created_by`. Spec 1 §7's schema gives `media_assets` a
        creator and deliberately gives `questions` none — a question is
        bank content, not a user's artifact, and Spec 2's analytics read
        its statistics rather than its authorship. Threading an admin id
        in here would be a parameter the row has nowhere to put.
        """
        ...
    async def update(
        self, question_id: str, write: QuestionWrite
    ) -> QuestionDetailRecord | None: ...
    async def set_active(
        self, question_id: str, *, is_active: bool
    ) -> QuestionDetailRecord | None: ...

    async def duplicates_of(self, prompt: str, *, excluding: str | None = None) -> tuple[str, ...]:
        """§10.2: a duplicate prompt is a warning, not a block —
        legitimately similar phrasings exist. The comparison is
        `prompt_digest`, the same whitespace- and case-insensitive hash
        `seed-questions` already uses."""
        ...

    async def existing_prompt_digests(self, digests: frozenset[str]) -> frozenset[str]:
        """Which of these the bank already has, in one query.

        The import's warning channel (Task 7) asks this once per upload
        rather than calling `duplicates_of` per row — same rule, same
        digest, one round trip.
        """
        ...


@dataclass(frozen=True)
class CategoryRecord:
    category_id: str
    slug: str
    name: str


class SlugTaken(Exception):
    """A category with that slug exists. Raised by the repository rather
    than reported as a bool, because it is the *only* failure `create` has
    and a bool return would put the burden of remembering that on every
    caller."""


class CategoryPort(Protocol):
    async def list(self) -> tuple[CategoryRecord, ...]: ...
    async def create(self, *, slug: str, name: str) -> CategoryRecord: ...
    async def rename(self, category_id: str, *, name: str) -> CategoryRecord | None: ...
