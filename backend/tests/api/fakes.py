"""In-memory stores for the Layer 3 contract suite.

Not a shortcut: §12.3's tests are about the *contract* — envelopes, status
codes, strictness, actor derivation, close codes — and none of that is a
property of PostgreSQL. Running them against a database and argon2 would
add a container and ~50 ms per login to a suite whose value depends on
being run on every change.
"""

import hashlib
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

from triviador.api.ws.hub import Hub
from triviador.db.repositories.questions import prompt_digest
from triviador.db.security import token_digest
from triviador.domain.game.rules import DEFAULT_RULES, GameRules
from triviador.domain.ids import GameId, MapId, PlayerId, SessionId, UserId
from triviador.domain.questions.types import QuestionKind
from triviador.services.admin import (
    CategoryNotFound,
    CategoryRecord,
    ChoiceRecord,
    DeactivateOutcome,
    ImportedImage,
    ImportedQuestion,
    ImportRecord,
    ImportStatus,
    InviteRecord,
    InviteStatus,
    MediaAssetNotFound,
    MediaAssetRecord,
    PresetAdminRecord,
    QuestionDetailRecord,
    QuestionFilters,
    QuestionPage,
    QuestionSummaryRecord,
    QuestionWrite,
    SetRoleOutcome,
    SlugTaken,
    UpdateOutcome,
)
from triviador.services.identity import (
    AuthenticatedPrincipal,
    RedeemOutcome,
    UserRecord,
    UserRole,
)
from triviador.services.ports import GameSummary, PresetRecord
from triviador.services.storage import ObjectHead, StoredObject

T0 = datetime(2026, 8, 18, 12, 0, tzinfo=UTC)


class FakeClock:
    """The API's own clock. Distinct from `tests/runtime/fakes.FakeClock`,
    which additionally drives `sleep_until` for the consumer loop; nothing
    in the HTTP layer sleeps."""

    def __init__(self, now: datetime = T0) -> None:
        self._now = now

    def now(self) -> datetime:
        return self._now

    async def sleep_until(self, when: datetime) -> None:
        self._now = max(self._now, when)

    def advance(self, delta: timedelta) -> None:
        self._now += delta


class FakeDatabase:
    """`DatabaseProbe`. `pings` exists so `test_liveness_never_touches_the
    _database` can assert the *absence* of a call rather than inferring it
    from a status code that would be 200 either way."""

    def __init__(self, reachable: bool = True) -> None:
        self.reachable = reachable
        self.pings = 0

    async def ping(self) -> bool:
        self.pings += 1
        return self.reachable


class FakeHasher:
    """A digest with a marker prefix, not the password with a prefix.

    `f"hashed:{password}"` would have made
    `test_a_stored_password_is_never_the_password` vacuously false — strip
    the prefix and the clear password is what is left. A digest keeps the
    assertion meaningful while costing microseconds instead of argon2's
    deliberate ~50 ms.
    """

    def hash(self, password: str) -> str:
        return "fake$" + hashlib.sha256(password.encode("utf-8")).hexdigest()

    def __init__(self) -> None:
        self.verifications = 0

    def verify(self, password: str, hashed: str) -> bool:
        self.verifications += 1
        return hashed == self.hash(password)


class RecordingHub(Hub):
    """The real `Hub`, plus a record of every `close_sessions` call.

    Task 11's tests assert *that* a socket was told to close and with
    which code, not the socket-plumbing details `Hub` itself already has
    unit tests for — recording the call is cheaper and more direct than
    wiring up a fake `Socket` and inspecting its `close_code`.
    """

    def __init__(self) -> None:
        super().__init__()
        self.closed: list[tuple[tuple[SessionId, ...], int]] = []

    def close_sessions(self, session_ids: Iterable[SessionId], code: int) -> None:
        ids = tuple(session_ids)
        self.closed.append((ids, code))
        super().close_sessions(ids, code)


@dataclass
class FakeUsers:
    records: dict[UserId, UserRecord] = field(default_factory=dict)

    async def create(
        self,
        *,
        user_id: UserId,
        username: str,
        password_hash: str,
        display_name: str,
        role: UserRole,
    ) -> None:
        self.records[user_id] = UserRecord(
            user_id, username, display_name, role, True, password_hash
        )

    async def get(self, user_id: UserId) -> UserRecord | None:
        return self.records.get(user_id)

    async def get_by_username(self, username: str) -> UserRecord | None:
        return next((r for r in self.records.values() if r.username == username), None)

    async def count_admins(self) -> int:
        return sum(1 for r in self.records.values() if r.role is UserRole.ADMIN and r.is_active)

    def deactivate(self, user_id: UserId) -> None:
        self.records[user_id] = replace(self.records[user_id], is_active=False)


@dataclass
class FakeSessions:
    users: FakeUsers
    rows: dict[str, tuple[SessionId, UserId, datetime, datetime | None]] = field(
        default_factory=dict
    )

    async def create(
        self, *, session_id: SessionId, user_id: UserId, token_hash: str, expires_at: datetime
    ) -> None:
        self.rows[token_hash] = (session_id, user_id, expires_at, None)

    async def resolve(self, token_hash: str, *, now: datetime) -> AuthenticatedPrincipal | None:
        row = self.rows.get(token_hash)
        if row is None:
            return None
        session_id, user_id, expires_at, revoked_at = row
        user = self.users.records.get(user_id)
        if revoked_at is not None or expires_at <= now or user is None or not user.is_active:
            return None
        return AuthenticatedPrincipal(user_id, user.role, session_id)

    async def revoke(self, session_id: SessionId, *, at: datetime) -> None:
        for token_hash, (sid, uid, exp, rev) in list(self.rows.items()):
            if sid == session_id and rev is None:
                self.rows[token_hash] = (sid, uid, exp, at)

    async def revoke_for_user(self, user_id: UserId, *, at: datetime) -> tuple[SessionId, ...]:
        closed = []
        for token_hash, (sid, uid, exp, rev) in list(self.rows.items()):
            if uid == user_id and rev is None:
                self.rows[token_hash] = (sid, uid, exp, at)
                closed.append(sid)
        return tuple(closed)


@dataclass
class FakeUserAdmin:
    """In-memory `UserAdminPort`, over the *same* `FakeUsers`/`FakeSessions`
    instances `deps.users`/`deps.sessions` use — mirroring the real
    `UserAdminRepository`, which reads and writes the same `users` and
    `sessions` tables the identity path (`UserRepository`/
    `SessionRepository`) does.
    """

    users: FakeUsers
    sessions: FakeSessions

    async def list(self) -> tuple[UserRecord, ...]:
        return tuple(sorted(self.users.records.values(), key=lambda r: r.username))

    async def get(self, user_id: UserId) -> UserRecord | None:
        return self.users.records.get(user_id)

    async def deactivate(self, user_id: UserId, *, at: datetime) -> tuple[SessionId, ...] | None:
        record = self.users.records.get(user_id)
        if record is None:
            return None
        self.users.records[user_id] = replace(record, is_active=False)
        return await self.sessions.revoke_for_user(user_id, at=at)

    async def set_role(
        self, user_id: UserId, *, role: UserRole, at: datetime
    ) -> tuple[SetRoleOutcome, tuple[SessionId, ...]]:
        record = self.users.records.get(user_id)
        if record is None:
            return SetRoleOutcome.NOT_FOUND, ()
        admins = sum(
            1 for r in self.users.records.values() if r.role is UserRole.ADMIN and r.is_active
        )
        if role is UserRole.PLAYER and record.role is UserRole.ADMIN and admins <= 1:
            return SetRoleOutcome.LAST_ADMIN, ()
        if record.role is role:
            return SetRoleOutcome.OK, ()
        self.users.records[user_id] = replace(record, role=role)
        revoked = await self.sessions.revoke_for_user(user_id, at=at)
        return SetRoleOutcome.OK, revoked


@dataclass
class _InviteEntry:
    """One `issue()`-created invite, tracked only for the admin surface —
    a code a test drops straight into `FakeInvites.valid` (as
    `test_auth.py`'s `register` helper does) has no entry here and is
    invisible to `list_all`/`revoke`, exactly as a real invite that never
    went through `InviteRepository.issue` would be invisible to the admin
    listing."""

    invite_id: str
    code_hash: str
    expires_at: datetime
    used_by: str | None = None
    revoked_at: datetime | None = None


@dataclass
class FakeInvites:
    """Implements both `InviteStore` (`redeem`) and `InviteAdminPort`
    (`issue`/`list_all`/`revoke`) — the same one-instance-two-ports shape
    `InviteRepository` has for real, so `deps.invites` and
    `deps.invites_admin` are the same object in the `deps` fixture too."""

    users: FakeUsers
    valid: dict[str, bool] = field(default_factory=dict)  # code_hash -> unused
    entries: dict[str, _InviteEntry] = field(default_factory=dict)  # invite_id -> entry

    async def redeem(
        self,
        *,
        code_hash: str,
        user_id: UserId,
        username: str,
        password_hash: str,
        display_name: str,
        now: datetime,
    ) -> RedeemOutcome:
        if not self.valid.get(code_hash, False):
            return RedeemOutcome.INVITE_INVALID
        if await self.users.get_by_username(username) is not None:
            # Mirrors the real repository: the claim rolls back with the
            # insert, so the invite stays usable.
            return RedeemOutcome.USERNAME_TAKEN
        self.valid[code_hash] = False
        await self.users.create(
            user_id=user_id,
            username=username,
            password_hash=password_hash,
            display_name=display_name,
            role=UserRole.PLAYER,
        )
        for entry in self.entries.values():
            if entry.code_hash == code_hash:
                entry.used_by = user_id
        return RedeemOutcome.OK

    async def issue(
        self, *, count: int, expires_at: datetime, created_by: UserId
    ) -> tuple[tuple[str, str], ...]:
        issued: list[tuple[str, str]] = []
        for _ in range(count):
            invite_id = str(uuid4())
            code = uuid4().hex
            code_hash = token_digest(code)
            self.valid[code_hash] = True
            self.entries[invite_id] = _InviteEntry(
                invite_id=invite_id,
                code_hash=code_hash,
                expires_at=expires_at,
            )
            issued.append((invite_id, code))
        return tuple(issued)

    async def list_all(self, *, now: datetime) -> tuple[InviteRecord, ...]:
        return tuple(
            InviteRecord(
                invite_id=entry.invite_id,
                status=_fake_invite_status(entry, now=now),
                expires_at=entry.expires_at,
                used_by=entry.used_by,
            )
            for entry in self.entries.values()
        )

    async def revoke(self, invite_id: str, *, at: datetime) -> bool:
        entry = self.entries.get(invite_id)
        if entry is None:
            return False
        if entry.revoked_at is None:
            entry.revoked_at = at
            self.valid[entry.code_hash] = False
        return True


def _fake_invite_status(entry: _InviteEntry, *, now: datetime) -> InviteStatus:
    if entry.used_by is not None:
        return "used"
    if entry.revoked_at is not None:
        return "revoked"
    return "expired" if entry.expires_at <= now else "pending"


@dataclass
class FakeGameCatalog:
    """`GameCatalogPort`. `created` records the keyword arguments verbatim,
    so a test can assert on `map_sha256` and `preset_id` — the two fields
    that are wrong-but-plausible if creation is miswired, and that no
    later request would reveal."""

    created: list[dict[str, object]] = field(default_factory=list)
    summaries: dict[GameId, GameSummary] = field(default_factory=dict)

    async def create(self, **kwargs: object) -> None:
        self.created.append(kwargs)
        game_id, map_id, host_id = kwargs["game_id"], kwargs["map_id"], kwargs["host_id"]
        assert isinstance(game_id, str)
        assert isinstance(map_id, str)
        assert isinstance(host_id, str)
        self.summaries[GameId(game_id)] = GameSummary(
            game_id=GameId(game_id),
            map_id=MapId(map_id),
            host_id=PlayerId(host_id),
            status="lobby",
            max_players=3,
            player_count=1,
            created_at=T0,
        )

    async def get_summary(self, game_id: GameId) -> GameSummary | None:
        return self.summaries.get(game_id)

    async def list_joinable(self) -> tuple[GameSummary, ...]:
        return tuple(s for s in self.summaries.values() if s.status == "lobby")


@dataclass
class FakePresets:
    """`PresetPort` and `PresetAdminPort` in one instance — the same
    pattern the real `PresetRepository` follows (see
    `PresetAdminRecord`'s docstring). Two presets seeded: `default` (three
    players, `DEFAULT_RULES`, the one `is_default`) and `two-player`,
    which exists because a test that wants to assert *authorization* on
    `start` must not be blocked by `NOT_ENOUGH_PLAYERS`."""

    presets: dict[str, PresetAdminRecord] = field(
        default_factory=lambda: {
            "default": PresetAdminRecord("default", "Default", DEFAULT_RULES, True, True),
            "two-player": PresetAdminRecord(
                "two-player",
                "Two",
                replace(DEFAULT_RULES, player_count=2, claims_by_rank=(2, 1)),
                False,
                True,
            ),
        }
    )

    async def get(self, preset_id: str) -> PresetAdminRecord | None:
        record = self.presets.get(preset_id)
        return record if record is not None and record.is_active else None

    async def get_including_retired(self, preset_id: str) -> PresetAdminRecord | None:
        return self.presets.get(preset_id)

    async def get_default(self) -> PresetAdminRecord | None:
        return next(
            (r for r in self.presets.values() if r.is_default and r.is_active), None
        )

    async def list_active(self) -> tuple[PresetRecord, ...]:
        return tuple(
            PresetRecord(r.preset_id, r.name, r.rules)
            for r in sorted(self.presets.values(), key=lambda r: r.name)
            if r.is_active
        )

    async def list_all(self) -> tuple[PresetAdminRecord, ...]:
        return tuple(sorted(self.presets.values(), key=lambda r: r.name))

    async def create(
        self, *, name: str, rules: GameRules, is_default: bool
    ) -> PresetAdminRecord:
        if is_default:
            self._clear_default()
        record = PresetAdminRecord(str(uuid4()), name, rules, is_default, True)
        self.presets[record.preset_id] = record
        return record

    async def update(
        self, preset_id: str, *, name: str, rules: GameRules, is_default: bool
    ) -> tuple[UpdateOutcome, PresetAdminRecord | None]:
        row = self.presets.get(preset_id)
        if row is None:
            return UpdateOutcome.NOT_FOUND, None
        if row.is_default and not is_default:
            return UpdateOutcome.WOULD_LEAVE_NO_DEFAULT, None
        if is_default and not row.is_active:
            return UpdateOutcome.RETIRED_CANNOT_BE_DEFAULT, None
        if is_default and not row.is_default:
            self._clear_default()
        record = PresetAdminRecord(preset_id, name, rules, is_default, row.is_active)
        self.presets[preset_id] = record
        return UpdateOutcome.OK, record

    async def deactivate(self, preset_id: str) -> DeactivateOutcome:
        row = self.presets.get(preset_id)
        if row is None:
            return DeactivateOutcome.NOT_FOUND
        if row.is_default:
            return DeactivateOutcome.IS_DEFAULT
        self.presets[preset_id] = replace(row, is_active=False)
        return DeactivateOutcome.OK

    def _clear_default(self) -> None:
        for preset_id, row in self.presets.items():
            if row.is_default:
                self.presets[preset_id] = replace(row, is_default=False)


class FakeMediaStore:
    """In-memory `MediaStore`. Keeps `put` calls so a test can assert the
    `Cache-Control` the route asked for without a live Garage."""

    def __init__(self, clock: FakeClock | None = None) -> None:
        self.objects: dict[str, bytes] = {}
        self.metadata: dict[str, tuple[str, str | None]] = {}
        # Write times, so a test can age an object past the gc grace
        # period without sleeping.
        self.written: dict[str, datetime] = {}
        self._clock = clock or FakeClock()

    async def put(
        self, key: str, data: bytes, *, content_type: str, cache_control: str | None = None
    ) -> None:
        self.objects[key] = data
        self.metadata[key] = (content_type, cache_control)
        self.written[key] = self._clock.now()

    async def open(self, key: str) -> bytes | None:
        return self.objects.get(key)

    async def head(self, key: str) -> ObjectHead | None:
        if key not in self.objects:
            return None
        content_type, cache_control = self.metadata[key]
        return ObjectHead(
            len(self.objects[key]), content_type, cache_control, self.written[key]
        )

    async def delete(self, key: str) -> None:
        self.objects.pop(key, None)

    async def list_objects(self, *, prefix: str = "") -> tuple[StoredObject, ...]:
        return tuple(
            StoredObject(key=key, byte_size=len(self.objects[key]), last_modified=self.written[key])
            for key in sorted(self.objects)
            if key.startswith(prefix)
        )


class FakeMediaAssets:
    """In-memory `MediaAssetPort`."""

    def __init__(self) -> None:
        self.records: dict[str, MediaAssetRecord] = {}

    async def ensure(self, **kwargs: object) -> tuple[MediaAssetRecord, bool]:
        asset_id = str(kwargs["asset_id"])
        if asset_id in self.records:
            return self.records[asset_id], False
        record = MediaAssetRecord(
            asset_id=asset_id,
            mime_type=str(kwargs["mime_type"]),
            width=int(kwargs["width"]),  # type: ignore[call-overload]
            height=int(kwargs["height"]),  # type: ignore[call-overload]
            byte_size=int(kwargs["byte_size"]),  # type: ignore[call-overload]
            storage_key=str(kwargs["storage_key"]),
        )
        self.records[asset_id] = record
        return record, True

    async def get(self, asset_id: str) -> MediaAssetRecord | None:
        return self.records.get(asset_id)

    async def unreferenced(self) -> tuple[MediaAssetRecord, ...]:
        """No route under `tests/api/` exercises `media-gc` — it is a CLI
        command, not an HTTP one (§10.4) — so nothing here tracks which
        records a question or event actually names. Every record counts
        as unreferenced, which is honest for a fake nothing ever attaches
        to anything: `tests/db/test_media_gc.py` is what proves the real
        two-way check against actual questions and events."""
        return tuple(self.records.values())

    async def claim_unreferenced(self) -> tuple[MediaAssetRecord, ...]:
        claimed = tuple(self.records.values())
        self.records.clear()
        return claimed

    async def all_storage_keys(self) -> frozenset[str]:
        return frozenset(r.storage_key for r in self.records.values())

    async def delete(self, asset_id: str) -> None:
        self.records.pop(asset_id, None)


def _to_summary(record: QuestionDetailRecord, *, updated_at: datetime) -> QuestionSummaryRecord:
    """`QuestionSummaryRecord` and `QuestionDetailRecord` share every field
    except `has_media`/`updated_at` (summary only) and
    `choices`/`numeric_answer`/`unit`/`media_asset_id` (detail only) — the
    fake keeps one dict of the wider shape and derives the narrower one for
    `.list`, rather than keeping two dicts that could drift apart."""
    return QuestionSummaryRecord(
        question_id=record.question_id,
        kind=record.kind,
        prompt=record.prompt,
        category_id=record.category_id,
        category_slug=record.category_slug,
        difficulty=record.difficulty,
        is_active=record.is_active,
        has_media=record.media_asset_id is not None,
        version=record.version,
        updated_at=updated_at,
    )


@dataclass
class FakeQuestionAdmin:
    """In-memory `QuestionAdminPort`. `last_filters` records whatever the
    route last called `.list` with, so a test can assert the query string
    actually reached the repository call rather than just the status code."""

    records: dict[str, QuestionDetailRecord] = field(default_factory=dict)
    last_filters: QuestionFilters | None = None
    # Empty by default, so every existing test's `write.category_id`/
    # `write.media_asset_id` keeps sailing through unchecked. A test that
    # wants to see the route's 404 translation (Important #1) populates
    # one of these with the id its request body carries — this fake has
    # no foreign key of its own to violate, so this is what stands in for
    # "that id no longer names a row".
    missing_category_ids: frozenset[str] = frozenset()
    missing_media_asset_ids: frozenset[str] = frozenset()

    async def list(self, filters: QuestionFilters, *, limit: int, offset: int) -> QuestionPage:
        self.last_filters = filters
        items = tuple(_to_summary(r, updated_at=T0) for r in self.records.values())
        return QuestionPage(items=items[offset : offset + limit], total=len(items))

    async def get(self, question_id: str) -> QuestionDetailRecord | None:
        return self.records.get(question_id)

    def _check_fks(self, write: QuestionWrite) -> None:
        if write.category_id in self.missing_category_ids:
            raise CategoryNotFound(write.category_id)
        asset_id = write.media_asset_id
        if asset_id is not None and asset_id in self.missing_media_asset_ids:
            raise MediaAssetNotFound(asset_id)

    async def create(self, write: QuestionWrite) -> QuestionDetailRecord:
        self._check_fks(write)
        record = QuestionDetailRecord(
            question_id=str(uuid4()),
            kind=write.kind,
            prompt=write.prompt,
            category_id=write.category_id,
            category_slug=write.category_id,
            difficulty=write.difficulty,
            is_active=True,
            version=1,
            media_asset_id=write.media_asset_id,
            choices=_choice_records(write),
            numeric_answer=write.numeric_answer,
            unit=write.unit,
        )
        self.records[record.question_id] = record
        return record

    async def update(self, question_id: str, write: QuestionWrite) -> QuestionDetailRecord | None:
        self._check_fks(write)
        existing = self.records.get(question_id)
        if existing is None:
            return None
        updated = QuestionDetailRecord(
            question_id=question_id,
            kind=write.kind,
            prompt=write.prompt,
            category_id=write.category_id,
            category_slug=existing.category_slug,
            difficulty=write.difficulty,
            is_active=existing.is_active,
            version=existing.version + 1,
            media_asset_id=write.media_asset_id,
            choices=_choice_records(write),
            numeric_answer=write.numeric_answer,
            unit=write.unit,
        )
        self.records[question_id] = updated
        return updated

    async def set_active(
        self, question_id: str, *, is_active: bool
    ) -> QuestionDetailRecord | None:
        existing = self.records.get(question_id)
        if existing is None:
            return None
        updated = replace(existing, is_active=is_active)
        self.records[question_id] = updated
        return updated

    async def duplicates_of(self, prompt: str, *, excluding: str | None = None) -> tuple[str, ...]:
        digest = prompt_digest(prompt)
        return tuple(
            record.question_id
            for record in self.records.values()
            if record.question_id != excluding and prompt_digest(record.prompt) == digest
        )

    async def existing_prompt_digests(self, digests: frozenset[str]) -> frozenset[str]:
        bank_digests = {prompt_digest(r.prompt) for r in self.records.values()}
        return frozenset(digest for digest in digests if digest in bank_digests)

    async def active_counts(self) -> dict[str, int]:
        counts = {kind.value: 0 for kind in QuestionKind}
        for record in self.records.values():
            if record.is_active:
                counts[record.kind] = counts.get(record.kind, 0) + 1
        return counts


def _choice_records(write: QuestionWrite) -> tuple[ChoiceRecord, ...] | None:
    if write.choices is None:
        return None
    return tuple(
        ChoiceRecord(idx, text, is_correct, None)
        for idx, (text, is_correct) in enumerate(write.choices)
    )


@dataclass
class FakeCategories:
    """In-memory `CategoryPort`.

    Unlike `FakeQuestionAdmin.list`, which ignores its `filters` argument,
    this fake mirrors the two behaviours the admin route contract actually
    depends on: `create` raises `SlugTaken` on a duplicate slug rather than
    silently overwriting, and `list` comes back ordered by slug the way
    `CategoryRepository.list`'s `ORDER BY categories.slug` does.
    """

    records: dict[str, CategoryRecord] = field(default_factory=dict)

    async def list(self) -> tuple[CategoryRecord, ...]:
        return tuple(sorted(self.records.values(), key=lambda r: r.slug))

    async def create(self, *, slug: str, name: str) -> CategoryRecord:
        if any(r.slug == slug for r in self.records.values()):
            raise SlugTaken(slug)
        record = CategoryRecord(category_id=str(uuid4()), slug=slug, name=name)
        self.records[record.category_id] = record
        return record

    async def rename(self, category_id: str, *, name: str) -> CategoryRecord | None:
        existing = self.records.get(category_id)
        if existing is None:
            return None
        updated = replace(existing, name=name)
        self.records[category_id] = updated
        return updated


@dataclass
class FakeImports:
    """In-memory `ImportPort`. Every import created here starts
    `ImportStatus.VALIDATED`.

    `categories`, `questions_admin` and `media_assets` are the *same*
    instances wired onto `AppDependencies` — never private copies. The real
    `QuestionImportRepository.apply_if_confirmable` writes straight into
    `categories`/`questions`/`media_assets` from inside its own locked
    transaction, bypassing `CategoryPort`/`QuestionAdminPort` entirely
    (Task 8's report explains why). This fake has no session to bypass, so
    it reaches the same stores those ports already write to — the only way
    a test asserting on `deps.questions_admin.records` after a confirm can
    see what the import produced.
    """

    records: dict[str, ImportRecord] = field(default_factory=dict)
    categories: FakeCategories = field(default_factory=FakeCategories)
    questions_admin: FakeQuestionAdmin = field(default_factory=FakeQuestionAdmin)
    media_assets: FakeMediaAssets = field(default_factory=FakeMediaAssets)

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
    ) -> ImportRecord:
        record = ImportRecord(
            import_id=import_id,
            uploaded_by=uploaded_by,
            upload_sha256=upload_sha256,
            filename=filename,
            staged_key=staged_key,
            row_count=row_count,
            rejected_count=rejected_count,
            report=report,
            status=ImportStatus.VALIDATED,
            expires_at=expires_at,
        )
        self.records[import_id] = record
        return record

    async def get(self, import_id: str) -> ImportRecord | None:
        return self.records.get(import_id)

    def add(
        self,
        import_id: str,
        *,
        status: ImportStatus,
        staged_key: str | None,
        expires_at: datetime,
        uploaded_by: str = "admin-1",
        upload_sha256: str = "0" * 64,
        filename: str = "questions.csv",
        row_count: int = 1,
        rejected_count: int = 0,
        report: dict[str, Any] | None = None,
    ) -> ImportRecord:
        """`tests/imports/test_retire.py`'s seam: places a row directly in
        whatever state §9.3 says it should already be in, rather than
        walking it there through `create`/`apply_if_confirmable` — the
        retirement machine's tests are about what happens *from* a given
        state, not about how a row gets there."""
        record = ImportRecord(
            import_id=import_id,
            uploaded_by=uploaded_by,
            upload_sha256=upload_sha256,
            filename=filename,
            staged_key=staged_key,
            row_count=row_count,
            rejected_count=rejected_count,
            report=report or {},
            status=status,
            expires_at=expires_at,
        )
        self.records[import_id] = record
        return record

    async def count_expirable(self, now: datetime, *, all_unconfirmed: bool) -> int:
        return sum(
            1
            for r in self.records.values()
            if r.status is ImportStatus.VALIDATED and (all_unconfirmed or r.expires_at < now)
        )

    async def mark_expired(self, now: datetime, *, all_unconfirmed: bool) -> int:
        count = 0
        for import_id, record in list(self.records.items()):
            if record.status is not ImportStatus.VALIDATED:
                continue
            if not all_unconfirmed and record.expires_at >= now:
                continue
            self.records[import_id] = replace(record, status=ImportStatus.EXPIRED)
            count += 1
        return count

    async def retirable_staged(self) -> tuple[tuple[str, str], ...]:
        return tuple(
            (import_id, record.staged_key)
            for import_id, record in self.records.items()
            if record.staged_key is not None
            and record.status in (ImportStatus.EXPIRED, ImportStatus.CONFIRMED)
        )

    async def mark_cleaned(self, import_id: str) -> None:
        record = self.records.get(import_id)
        if record is None:
            return
        status = ImportStatus.CLEANED if record.status is ImportStatus.EXPIRED else record.status
        self.records[import_id] = replace(record, status=status, staged_key=None)

    async def apply_if_confirmable(
        self,
        import_id: str,
        *,
        rows: Sequence[ImportedQuestion],
        images: Mapping[str, ImportedImage],
        uploaded_by: str,
        now: datetime,
    ) -> bool:
        """Mirrors `QuestionImportRepository.apply_if_confirmable`'s three
        rechecked conditions and its category/asset/question writes — a
        single-process stand-in for the `FOR UPDATE` transaction, since
        nothing here is actually concurrent."""
        record = self.records.get(import_id)
        if record is None:
            return False
        if record.status is not ImportStatus.VALIDATED:
            return False
        if record.rejected_count != 0:
            return False
        if record.expires_at <= now:
            return False

        category_ids = {c.slug: c.category_id for c in self.categories.records.values()}
        for slug in {row.category_slug for row in rows} - set(category_ids):
            created = await self.categories.create(
                slug=slug, name=slug.replace("-", " ").title()
            )
            category_ids[slug] = created.category_id

        for image in images.values():
            await self.media_assets.ensure(
                asset_id=image.asset_id,
                mime_type=image.mime_type,
                width=image.width,
                height=image.height,
                byte_size=image.byte_size,
                storage_key=image.storage_key,
                created_by=uploaded_by,
            )

        for row in rows:
            await self.questions_admin.create(
                QuestionWrite(
                    kind=row.kind,
                    prompt=row.prompt,
                    category_id=category_ids[row.category_slug],
                    difficulty=row.difficulty,
                    media_asset_id=images[row.media_file].asset_id if row.media_file else None,
                    choices=row.choices,
                    numeric_answer=row.numeric_answer,
                    unit=row.unit,
                )
            )

        self.records[import_id] = replace(record, status=ImportStatus.CONFIRMED)
        return True


class FakeStagingStore:
    """In-memory `ImportStagingStore`. `objects` is asserted directly by
    the dry-run route tests, the same way `FakeMediaStore.objects` is."""

    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}
        self.metadata: dict[str, str] = {}

    async def put(self, key: str, data: bytes, *, content_type: str) -> None:
        self.objects[key] = data
        self.metadata[key] = content_type

    async def open(self, key: str) -> bytes | None:
        return self.objects.get(key)

    async def delete(self, key: str) -> None:
        self.objects.pop(key, None)
        self.metadata.pop(key, None)
