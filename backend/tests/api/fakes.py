"""In-memory stores for the Layer 3 contract suite.

Not a shortcut: §12.3's tests are about the *contract* — envelopes, status
codes, strictness, actor derivation, close codes — and none of that is a
property of PostgreSQL. Running them against a database and argon2 would
add a container and ~50 ms per login to a suite whose value depends on
being run on every change.
"""

import hashlib
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta

from triviador.domain.game.rules import DEFAULT_RULES
from triviador.domain.ids import GameId, MapId, PlayerId, SessionId, UserId
from triviador.services.admin import MediaAssetRecord
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
