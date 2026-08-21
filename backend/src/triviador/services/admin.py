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

from triviador.domain.game.rules import GameRules
from triviador.domain.ids import SessionId, UserId
from triviador.services.identity import UserRecord, UserRole
from triviador.services.ports import PresetRecord


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


class CategoryNotFound(Exception):
    """`create`/`update` named a `category_id` no `categories` row backs.

    Same shape as `SlugTaken` below: the repository raises this instead of
    letting the foreign-key violation surface as a raw `IntegrityError`,
    which the global handler maps to 503 `database_unavailable` — telling
    an admin the database is down when they simply posted a stale id."""


class MediaAssetNotFound(Exception):
    """`create`/`update` named a `media_asset_id` no `media_assets` row
    backs. The concrete case this exists for: `media-gc` deletes
    unreferenced `media_assets` rows on a sweep, and an editor tab left
    open across that sweep can still post the id it had on screen."""


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

    async def active_counts(self) -> dict[str, int]:
        """Active questions per kind — the bank half of a preset's
        coverage readout (§10.6, Plan 7A Task 12). The same shape
        `seed-questions` prints, computed the same way: one query,
        grouped."""
        ...


# The one shape a category slug is allowed to have — lowercase, dashed,
# never empty. Shared between `CreateCategoryRequest` (the interactive
# route, enforced by Pydantic's `pattern=`) and `imports/parse.py` (the
# bulk route, enforced by `re.fullmatch`), so there is exactly one rule
# rather than two that can drift: see Important #2 of the Plan 7A review,
# which found the importer bypassing this rule entirely. Lives here, not
# in `domain/`: a slug's spelling is an input-validation rule the two
# adapter-side callers share, not a game rule, and this plan leaves
# `domain/` untouched.
CATEGORY_SLUG_PATTERN = r"^[a-z0-9]+(-[a-z0-9]+)*$"


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


class SetRoleOutcome(StrEnum):
    """`UserAdminRepository.set_role`'s three outcomes.

    Lives here, not in `db/repositories/auth.py`: the route in
    `api/http/admin/users.py` has to name this enum, and every other admin
    route in this plan imports its outcome/status types from `services/`,
    never reaches into `db/` — the port module is where an `api/` route is
    allowed to look. `db/repositories/auth.py` imports it back from here.
    """

    OK = "ok"
    NOT_FOUND = "not_found"
    LAST_ADMIN = "last_admin"


class UserAdminPort(Protocol):
    async def list(self) -> tuple[UserRecord, ...]: ...

    async def get(self, user_id: UserId) -> UserRecord | None:
        """Used to build the response after `deactivate`/`set_role`
        mutate a row — those two hand back only what changed (revoked
        session ids), not the updated record itself."""
        ...

    async def deactivate(self, user_id: UserId, *, at: datetime) -> tuple[SessionId, ...] | None:
        """`None` means no such user. Otherwise, every session this call
        just revoked, for the caller to close with `Hub.close_sessions`
        after its own transaction commits (§10.5, §11.2's "committed
        before published")."""
        ...

    async def set_role(
        self, user_id: UserId, *, role: UserRole, at: datetime
    ) -> tuple[SetRoleOutcome, tuple[SessionId, ...]]:
        """The outcome, and — only on `OK` — the sessions a role change
        revoked. `LAST_ADMIN` and `NOT_FOUND` never revoke anything."""
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

    async def expirable_staged_count(self, now: datetime, *, all_unconfirmed: bool) -> int:
        """What a real `ImportRetirer.run()` would delete, for `media-gc
        --dry-run`'s `objects_deleted` figure.

        Not the same set `retirable_staged()` returns. A real run's second
        step only ever sees a row *after* `mark_expired` has already
        promoted it out of `validated`, so `retirable_staged()` alone —
        `expired`/`confirmed` rows with a `staged_key` — undercounts: a
        `validated` row whose `expires_at` has already passed (or every
        `validated` row, under `all_unconfirmed`) still owns a staged
        object that the real run is about to delete, and `--dry-run` has
        to say so *before* running `mark_expired`, not after. This unions
        that same still-`validated`-but-about-to-expire set — exactly
        what `count_expirable`'s `now`/`all_unconfirmed` already select —
        with `retirable_staged()`'s own rows, both restricted to a
        `staged_key` that is not null.
        """
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


@dataclass(frozen=True)
class PresetAdminRecord(PresetRecord):
    """`PresetRecord` plus the two flags only the admin screen needs.

    A subclass, not a sibling dataclass: `PresetRepository.get` and
    `.get_default` (already `PresetPort`) return exactly this type now, so
    `deps.presets` and `deps.presets_admin` can be the same instance —
    the pattern `InviteRepository`/`invites`/`invites_admin` already uses.
    That only type-checks because `PresetAdminRecord` *is a* `PresetRecord`
    (covariant return): `PresetPort.get() -> PresetRecord | None` accepts a
    method that actually returns the wider type, but not the reverse.
    """

    is_default: bool
    is_active: bool


class DeactivateOutcome(StrEnum):
    """`PresetAdminRepository.deactivate`'s three outcomes. `IS_DEFAULT` is
    Spec 1B §6.1's soft-delete rule: physically deleting a preset would
    break historical `games.preset_id`, so retirement is `is_active =
    false`, and the current default may never be retired — that would
    leave `POST /api/games` with `preset_id: null` answering
    `no_default_preset` to every player."""

    OK = "ok"
    NOT_FOUND = "not_found"
    IS_DEFAULT = "is_default"


class UpdateOutcome(StrEnum):
    """`PresetAdminRepository.update`'s five outcomes. `WOULD_LEAVE_NO_DEFAULT`
    and `RETIRED_CANNOT_BE_DEFAULT` are both about the same invariant —
    "never zero defaults, and never a retired one" — approached from
    opposite directions; see that method's docstring.

    `LOST_DEFAULT_RACE` is a third, purely concurrent way to fail the same
    invariant: two admins each promote a *different* preset to default at
    the same moment. Neither request is wrong on its own — each holds
    `FOR UPDATE` on its own target row, so they never block each other
    there — but both race `_clear_default`'s unconditional `UPDATE ...
    WHERE is_default = true`. The loser finds nothing left to clear once
    the winner commits, then its own `is_default = True` collides with the
    winner's row on `uq_rule_presets_single_default`. That is a
    `UniqueViolationError` surfacing an ordinary lost race, not a broken
    invariant (the final state is still exactly one default) and not a
    database outage — so it is caught here and turned into this outcome
    rather than left to reach the generic `SQLAlchemyError` handler as a
    misleading 503."""

    OK = "ok"
    NOT_FOUND = "not_found"
    WOULD_LEAVE_NO_DEFAULT = "would_leave_no_default"
    RETIRED_CANNOT_BE_DEFAULT = "retired_cannot_be_default"
    LOST_DEFAULT_RACE = "lost_default_race"


class CreateOutcome(StrEnum):
    """`PresetAdminRepository.create`'s two outcomes. `LOST_DEFAULT_RACE`
    is the same race `UpdateOutcome.LOST_DEFAULT_RACE` documents, from the
    creating side: a brand-new preset created with `is_default=True` can
    just as easily lose the race against a concurrent promotion as an
    edit can."""

    OK = "ok"
    LOST_DEFAULT_RACE = "lost_default_race"


class PresetAdminPort(Protocol):
    """§10.6's CRUD screen. `get` returns `PresetAdminRecord`, not the
    narrower `PresetPort.get`'s `PresetRecord` — see that class's
    docstring for why one repository method can satisfy both."""

    async def list_all(self) -> tuple[PresetAdminRecord, ...]: ...
    async def get(self, preset_id: str) -> PresetAdminRecord | None: ...

    async def get_including_retired(self, preset_id: str) -> PresetAdminRecord | None:
        """The admin's single-item read, which must see what the admin list sees.

        `PresetPort.get` filters on `is_active` — a player must never start
        a game on a retired preset — but `list_all` deliberately shows
        retired presets to an admin, and a detail view that 404s on exactly
        those rows makes the `is_active` field it renders unreachable. One
        repository satisfies both ports, so the two reads differ by name
        rather than by a flag.
        """
        ...

    async def create(
        self, *, name: str, rules: GameRules, is_default: bool
    ) -> tuple[CreateOutcome, PresetAdminRecord | None]: ...
    async def update(
        self, preset_id: str, *, name: str, rules: GameRules, is_default: bool
    ) -> tuple[UpdateOutcome, PresetAdminRecord | None]: ...
    async def deactivate(self, preset_id: str) -> DeactivateOutcome: ...
