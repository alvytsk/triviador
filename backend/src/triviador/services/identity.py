"""Who a request is, and the three stores that can answer it.

Declared here for the same reason as `ports.py`: `api/` depends on these
Protocols, `db/` implements them, and neither imports the other. The
practical payoff is that Layer 3's contract suite runs against in-memory
fakes with no PostgreSQL and no argon2 — a suite that costs 50 ms of
deliberate key-stretching per login is a suite that gets skipped.
"""

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Protocol

from triviador.domain.ids import SessionId, UserId


class UserRole(StrEnum):
    PLAYER = "player"
    ADMIN = "admin"


@dataclass(frozen=True)
class AuthenticatedPrincipal:
    """What a session proves. Spec 1B §6.5: a connection stores *this*, not
    a `ViewerContext` — the socket is multiplexed and one connection can
    hold different standing in different topics."""

    user_id: UserId
    role: UserRole
    session_id: SessionId


@dataclass(frozen=True)
class UserRecord:
    user_id: UserId
    username: str
    display_name: str
    role: UserRole
    is_active: bool
    password_hash: str


class RedeemOutcome(StrEnum):
    OK = "ok"
    INVITE_INVALID = "invite_invalid"
    USERNAME_TAKEN = "username_taken"


class PasswordHasher(Protocol):
    """`verify` returns a bool and never raises on a mismatch or on a
    malformed stored hash: a caller that has to distinguish exceptions to
    learn "wrong password" eventually catches the wrong one."""

    def hash(self, password: str) -> str: ...
    def verify(self, password: str, hashed: str) -> bool: ...


class UserStore(Protocol):
    async def create(
        self,
        *,
        user_id: UserId,
        username: str,
        password_hash: str,
        display_name: str,
        role: UserRole,
    ) -> None: ...
    async def get(self, user_id: UserId) -> UserRecord | None: ...
    async def get_by_username(self, username: str) -> UserRecord | None: ...
    async def count_admins(self) -> int: ...


class SessionStore(Protocol):
    async def create(
        self, *, session_id: SessionId, user_id: UserId, token_hash: str, expires_at: datetime
    ) -> None: ...

    async def resolve(self, token_hash: str, *, now: datetime) -> AuthenticatedPrincipal | None:
        """Live session, unexpired, unrevoked, belonging to an active user.

        One method rather than four, because "this session is dead" has
        four causes and a caller that has to assemble them is a caller that
        forgets one. `users.is_active` is part of it: Spec 1 §7 requires
        that deactivating a user log them out *now*, which is the entire
        reason sessions are a table instead of a JWT.
        """
        ...

    async def revoke(self, session_id: SessionId, *, at: datetime) -> None: ...

    async def revoke_for_user(self, user_id: UserId, *, at: datetime) -> tuple[SessionId, ...]:
        """Returns the sessions it closed, so the caller can close their
        sockets with `4401` (§6.5). Plan 7's deactivate endpoint is that
        caller; this plan provides the half that can be tested now."""
        ...


class InviteStore(Protocol):
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
        """Claim the invite and create the user, or neither.

        These are one method because they must be one transaction. Claiming
        first and creating second burns an invite when the username turns
        out to be taken; creating first and claiming second hands an account
        to whoever loses the race for the code. The implementation claims
        with a conditional `UPDATE ... WHERE used_by IS NULL RETURNING id`,
        which is also the concurrency check — two simultaneous redemptions
        of one code cannot both match it.
        """
        ...
