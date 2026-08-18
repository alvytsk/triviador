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

from triviador.domain.ids import SessionId, UserId
from triviador.services.identity import (
    AuthenticatedPrincipal,
    RedeemOutcome,
    UserRecord,
    UserRole,
)

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
