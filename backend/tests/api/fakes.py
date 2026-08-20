"""In-memory stores for the Layer 3 contract suite.

Not a shortcut: §12.3's tests are about the *contract* — envelopes, status
codes, strictness, actor derivation, close codes — and none of that is a
property of PostgreSQL. Running them against a database and argon2 would
add a container and ~50 ms per login to a suite whose value depends on
being run on every change.
"""

import hashlib
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

from triviador.db.repositories.questions import prompt_digest
from triviador.domain.game.rules import DEFAULT_RULES
from triviador.domain.ids import GameId, MapId, PlayerId, SessionId, UserId
from triviador.services.admin import (
    CategoryRecord,
    ChoiceRecord,
    ImportedImage,
    ImportedQuestion,
    ImportRecord,
    ImportStatus,
    MediaAssetRecord,
    QuestionDetailRecord,
    QuestionFilters,
    QuestionPage,
    QuestionSummaryRecord,
    QuestionWrite,
    SlugTaken,
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
class FakeInvites:
    users: FakeUsers
    valid: dict[str, bool] = field(default_factory=dict)  # code_hash -> unused

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
        return RedeemOutcome.OK


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
    """`PresetPort`. Two presets: `default` (three players, `DEFAULT_RULES`)
    and `two-player`, which exists because a test that wants to assert
    *authorization* on `start` must not be blocked by
    `NOT_ENOUGH_PLAYERS`."""

    presets: dict[str, PresetRecord] = field(
        default_factory=lambda: {
            "default": PresetRecord("default", "Default", DEFAULT_RULES),
            "two-player": PresetRecord(
                "two-player",
                "Two",
                replace(DEFAULT_RULES, player_count=2, claims_by_rank=(2, 1)),
            ),
        }
    )

    async def get(self, preset_id: str) -> PresetRecord | None:
        return self.presets.get(preset_id)

    async def get_default(self) -> PresetRecord | None:
        return self.presets.get("default")


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

    async def list(self, filters: QuestionFilters, *, limit: int, offset: int) -> QuestionPage:
        self.last_filters = filters
        items = tuple(_to_summary(r, updated_at=T0) for r in self.records.values())
        return QuestionPage(items=items[offset : offset + limit], total=len(items))

    async def get(self, question_id: str) -> QuestionDetailRecord | None:
        return self.records.get(question_id)

    async def create(self, write: QuestionWrite) -> QuestionDetailRecord:
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
