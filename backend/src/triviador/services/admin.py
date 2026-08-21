"""What the admin surface asks of the database, as Protocols.

Same rule as `ports.py` and `identity.py`: `api/` depends on these, `db/`
implements them, neither imports the other, and `tests/api/` runs the
whole admin surface against in-memory fakes with no PostgreSQL.

One port per resource rather than one `AdminPort` with thirty methods:
`tests/api/fakes.py` has to implement whatever a route touches, and a
single wide port would make every fake grow a method for every route in
the plan.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any, Literal, Protocol

from triviador.domain.ids import UserId


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

    async def unreferenced(self) -> tuple[MediaAssetRecord, ...]:
        """§10.4's two-way check, read-only: every asset named by neither a
        question, a choice, nor a persisted event snapshot. `media-gc
        --dry-run` reports this; the destructive sweep uses
        `claim_unreferenced` instead, which repeats the same check under a
        lock."""
        ...

    async def claim_unreferenced(self) -> tuple[MediaAssetRecord, ...]:
        """Delete every row `unreferenced()` would return, atomically, and
        hand them back so the caller can delete their objects next.

        Rows before objects, always: PostgreSQL and Garage share no
        transaction, so this is the half of `media-gc`'s sweep that can be
        made safe by a database lock, and it is the half that decides what
        a crash leaves behind — an object with no row, not a row pointing
        at a blob that is gone.
        """
        ...

    async def all_storage_keys(self) -> frozenset[str]:
        """Every key a `media_assets` row currently claims, for the orphan
        pass: a key `list_objects` finds with no row here is either
        garbage from a failed import transaction (§10.3) or an upload
        whose row has not committed yet, and `media-gc`'s grace period is
        what tells the two apart."""
        ...

    async def delete(self, asset_id: str) -> None:
        """Used only for an asset `claim_unreferenced` has already deleted
        the row of, if a caller ever needs to remove a row on its own —
        `claim_unreferenced` does its own deleting inline rather than
        calling this in a loop, to keep both operations in one
        transaction."""
        ...


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


class ImportStatus(StrEnum):
    """§9.3's four states, closed here because this plan implements the
    machine that walks them. Plan 3 left the column unconstrained on
    purpose — the spec named these in prose only — and `imports/retire.py`
    is now the single writer."""

    VALIDATED = "validated"
    CONFIRMED = "confirmed"
    EXPIRED = "expired"
    CLEANED = "cleaned"


@dataclass(frozen=True)
class ImportRecord:
    import_id: str
    uploaded_by: str
    upload_sha256: str
    filename: str
    staged_key: str | None
    row_count: int
    rejected_count: int
    report: dict[str, Any]
    status: ImportStatus
    expires_at: datetime


@dataclass(frozen=True)
class ImportedImage:
    """A blob the confirm has already written, described for the row that
    will reference it. No bytes: they are in the bucket by the time this
    exists."""

    asset_id: str
    mime_type: str
    width: int
    height: int
    byte_size: int
    storage_key: str


@dataclass(frozen=True)
class ImportedQuestion:
    """One row of a validated import, in the vocabulary of the bank.

    `category_slug` rather than `category_id`: the category may not exist
    until the confirming transaction creates it, so resolution has to
    happen inside that transaction and cannot be done by the caller.
    """

    category_slug: str
    kind: str
    prompt: str
    difficulty: str
    media_file: str | None
    choices: tuple[tuple[str, bool], ...] | None
    numeric_answer: Decimal | None
    unit: str | None


InviteStatus = Literal["pending", "used", "revoked", "expired"]


@dataclass(frozen=True)
class InviteRecord:
    """One `invite_codes` row, in the vocabulary of the admin listing.

    No `code`: the plaintext exists in exactly one response, `issue`'s, and
    a record type that could carry both would make it too easy for some
    future caller to leak it into a listing by accident.
    """

    invite_id: str
    status: InviteStatus
    expires_at: datetime
    used_by: str | None


class InviteAdminPort(Protocol):
    async def issue(
        self, *, count: int, expires_at: datetime, created_by: UserId
    ) -> tuple[tuple[str, str], ...]:
        """`(invite_id, code)` pairs — the only moment the plaintext exists
        anywhere outside the admin's clipboard."""
        ...

    async def list_all(self, *, now: datetime) -> tuple[InviteRecord, ...]: ...

    async def revoke(self, invite_id: str, *, at: datetime) -> bool:
        """`True` if the invite exists, whether or not this call is the one
        that revoked it — see `InviteRepository.revoke`'s docstring."""
        ...


class ImportPort(Protocol):
    async def create(
        self,
        *,
        import_id: str,
        uploaded_by: str,
        upload_sha256: str,
        filename: str,
        staged_key: str,
        row_count: int,
        rejected_count: int,
        report: dict[str, Any],
        expires_at: datetime,
    ) -> ImportRecord: ...
    async def get(self, import_id: str) -> ImportRecord | None: ...

    async def apply_if_confirmable(
        self,
        import_id: str,
        *,
        rows: Sequence[ImportedQuestion],
        images: Mapping[str, ImportedImage],
        uploaded_by: str,
        now: datetime,
    ) -> bool:
        """§9.3's transaction, from `FOR UPDATE` to `COMMIT`.

        Everything the import inserts — categories, questions, choices,
        numeric answers, media asset rows — happens inside this call,
        because it all has to be inside the transaction that holds the
        lock. Passing plain data rather than a callback keeps the
        SQLAlchemy session on the `db/` side of the port: a Protocol whose
        parameter is a session either names `AsyncSession` in `services/`
        (which the layering gate forbids) or widens it to `object`, which
        no implementation can narrow back without breaking
        contravariance — `mypy --strict` rejects both.

        `False` means the row was not confirmable under the lock: already
        confirmed, expired, or carrying rejections. The caller turns that
        into a 409; it is never an exception, because losing this race is
        an ordinary outcome of two admins clicking at once.
        """
        ...

    async def count_expirable(self, now: datetime, *, all_unconfirmed: bool) -> int:
        """What `mark_expired` would touch, for `media-gc --dry-run`.
        `all_unconfirmed` mirrors that method's flag exactly, so the
        preview and the real run always agree on the count."""
        ...

    async def mark_expired(self, now: datetime, *, all_unconfirmed: bool) -> int:
        """§9.3's first step: `validated` -> `expired`, never touching the
        staged object. `all_unconfirmed` is `--after-restore` (§10.9):
        staging is not backed up, so every `validated` row that survived a
        restore is unconfirmable regardless of its own `expires_at`."""
        ...

    async def retirable_staged(self) -> tuple[tuple[str, str], ...]:
        """Every `(import_id, staged_key)` still holding an object:
        `expired` rows (§9.3's second step still owes them a delete) and
        `confirmed` rows (whose upload the bank no longer needs). A row
        already `cleaned` has `staged_key = NULL` and so never appears
        here — that column is what makes this call, and the whole
        machine, idempotent."""
        ...

    async def mark_cleaned(self, import_id: str) -> None:
        """§9.3's third step, run once the staged object is confirmed
        gone: clear `staged_key`, and move `expired` to `cleaned`.
        `confirmed` stays `confirmed` — that row is the audit trail."""
        ...
