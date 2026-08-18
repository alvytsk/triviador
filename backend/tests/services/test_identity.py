"""The auth ports, proved by construction rather than by assertion.

`tests/services/test_ports.py` established the pattern: a minimal class per
Protocol, assigned to a variable of the Protocol type. If the shape is
wrong, `mypy --strict` fails; the runtime assertions below only prove the
module imports and the enums hold the values the rest of the plan spells.
"""

from datetime import UTC, datetime

from triviador.domain.ids import SessionId, UserId
from triviador.services.identity import (
    AuthenticatedPrincipal,
    InviteStore,
    PasswordHasher,
    RedeemOutcome,
    SessionStore,
    UserRecord,
    UserRole,
    UserStore,
)

NOW = datetime(2026, 8, 18, 12, 0, tzinfo=UTC)


class NullHasher:
    def hash(self, password: str) -> str:
        return password

    def verify(self, password: str, hashed: str) -> bool:
        return password == hashed


class NullUsers:
    async def create(
        self,
        *,
        user_id: UserId,
        username: str,
        password_hash: str,
        display_name: str,
        role: UserRole,
    ) -> None: ...

    async def get(self, user_id: UserId) -> UserRecord | None:
        return None

    async def get_by_username(self, username: str) -> UserRecord | None:
        return None

    async def count_admins(self) -> int:
        return 0


class NullSessions:
    async def create(
        self, *, session_id: SessionId, user_id: UserId, token_hash: str, expires_at: datetime
    ) -> None: ...

    async def resolve(self, token_hash: str, *, now: datetime) -> AuthenticatedPrincipal | None:
        return None

    async def revoke(self, session_id: SessionId, *, at: datetime) -> None: ...

    async def revoke_for_user(self, user_id: UserId, *, at: datetime) -> tuple[SessionId, ...]:
        return ()


class NullInvites:
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
        return RedeemOutcome.INVITE_INVALID


_hasher: PasswordHasher = NullHasher()
_users: UserStore = NullUsers()
_sessions: SessionStore = NullSessions()
_invites: InviteStore = NullInvites()


def test_the_roles_are_exactly_player_and_admin() -> None:
    assert {r.value for r in UserRole} == {"player", "admin"}


def test_a_principal_carries_the_session_it_came_from() -> None:
    """Not decoration: revoking one session must not log the user's other
    tabs out, so the id that authenticated *this* connection has to travel
    with it."""
    principal = AuthenticatedPrincipal(UserId("u1"), UserRole.PLAYER, SessionId("s1"))
    assert principal.session_id == SessionId("s1")


def test_redeeming_reports_which_of_the_two_things_went_wrong() -> None:
    assert {o.value for o in RedeemOutcome} == {"ok", "invite_invalid", "username_taken"}
