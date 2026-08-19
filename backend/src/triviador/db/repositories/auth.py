"""`UserRepository`, `SessionRepository`, `InviteRepository`.

Each implements the matching Protocol in `services/identity.py`. The only
non-obvious method is `InviteRepository.redeem`, which is one transaction
doing two things — see its docstring.
"""

from datetime import datetime

from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from triviador.db.models.auth import InviteCode, Session, User
from triviador.domain.ids import SessionId, UserId
from triviador.services.identity import (
    AuthenticatedPrincipal,
    RedeemOutcome,
    UserRecord,
    UserRole,
)


def _to_record(user: User) -> UserRecord:
    return UserRecord(
        user_id=UserId(user.id),
        username=user.username,
        display_name=user.display_name,
        role=UserRole(user.role),
        is_active=user.is_active,
        password_hash=user.password_hash,
    )


class UserRepository:
    def __init__(self, sessionmaker: async_sessionmaker[AsyncSession]) -> None:
        self._sessionmaker = sessionmaker

    async def create(
        self,
        *,
        user_id: UserId,
        username: str,
        password_hash: str,
        display_name: str,
        role: UserRole,
    ) -> None:
        async with self._sessionmaker() as session, session.begin():
            session.add(
                User(
                    id=user_id,
                    username=username,
                    password_hash=password_hash,
                    display_name=display_name,
                    role=str(role),
                    is_active=True,
                )
            )

    async def get(self, user_id: UserId) -> UserRecord | None:
        async with self._sessionmaker() as session:
            user = await session.get(User, user_id)
        return None if user is None else _to_record(user)

    async def get_by_username(self, username: str) -> UserRecord | None:
        async with self._sessionmaker() as session:
            result = await session.execute(select(User).where(User.username == username))
            user = result.scalar_one_or_none()
        return None if user is None else _to_record(user)

    async def count_admins(self) -> int:
        async with self._sessionmaker() as session:
            result = await session.execute(
                select(func.count())
                .select_from(User)
                .where(User.role == str(UserRole.ADMIN), User.is_active)
            )
            return result.scalar_one()


class SessionRepository:
    def __init__(self, sessionmaker: async_sessionmaker[AsyncSession]) -> None:
        self._sessionmaker = sessionmaker

    async def create(
        self, *, session_id: SessionId, user_id: UserId, token_hash: str, expires_at: datetime
    ) -> None:
        async with self._sessionmaker() as db, db.begin():
            db.add(
                Session(
                    id=session_id,
                    user_id=user_id,
                    token_hash=token_hash,
                    expires_at=expires_at,
                    revoked_at=None,
                )
            )

    async def resolve(self, token_hash: str, *, now: datetime) -> AuthenticatedPrincipal | None:
        """All four conditions in one statement.

        The join against `users` is what makes deactivation immediate: a
        second query would be a window in which a deactivated user's next
        request still succeeds.
        """
        async with self._sessionmaker() as db:
            result = await db.execute(
                select(Session.id, User.id, User.role)
                .join(User, User.id == Session.user_id)
                .where(
                    Session.token_hash == token_hash,
                    Session.revoked_at.is_(None),
                    Session.expires_at > now,
                    User.is_active,
                )
            )
            row = result.one_or_none()
        if row is None:
            return None
        session_id, user_id, role = row
        return AuthenticatedPrincipal(UserId(user_id), UserRole(role), SessionId(session_id))

    async def revoke(self, session_id: SessionId, *, at: datetime) -> None:
        async with self._sessionmaker() as db, db.begin():
            await db.execute(
                update(Session)
                .where(Session.id == session_id, Session.revoked_at.is_(None))
                .values(revoked_at=at)
            )

    async def revoke_for_user(self, user_id: UserId, *, at: datetime) -> tuple[SessionId, ...]:
        async with self._sessionmaker() as db, db.begin():
            result = await db.execute(
                update(Session)
                .where(Session.user_id == user_id, Session.revoked_at.is_(None))
                .values(revoked_at=at)
                .returning(Session.id)
            )
            return tuple(SessionId(i) for i in result.scalars().all())


class _InviteUnavailable(Exception):
    """The conditional claim matched no row. Private: it never escapes
    `redeem`, and it exists so the `IntegrityError` handler below can mean
    exactly one thing."""


class InviteRepository:
    def __init__(self, sessionmaker: async_sessionmaker[AsyncSession]) -> None:
        self._sessionmaker = sessionmaker

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
        """Create and claim, or neither.

        Create first, claim second — the reverse of what the naive reading
        of "claim, then create" would suggest, and deliberately so:
        `invite_codes.used_by` is a plain (non-`DEFERRABLE`) foreign key to
        `users.id`, and PostgreSQL checks a non-deferrable foreign key
        immediately, on the statement that would violate it, not at commit.
        Claiming first would set `used_by` to a `user_id` no row yet backs,
        which fails immediately with a `ForeignKeyViolationError` — not a
        `username`-uniqueness error — every single time, for every
        redemption. So the `INSERT` has to happen first, inside the same
        transaction, with `flush()` forcing it to run (and a duplicate
        `username` to raise) before the claim is even attempted.

        The claiming `UPDATE ... WHERE used_by IS NULL RETURNING id` is
        still both the business rule and the concurrency control: two
        simultaneous redemptions of one code each insert their own user,
        then contend on this row, and exactly one gets a returned id. The
        loser raises `_InviteUnavailable`, which unwinds the `async with
        db.begin()` block without committing — rolling back its `INSERT`
        along with the failed claim, so a lost race never leaves an orphan
        `users` row. That private exception is also what keeps `except
        IntegrityError` unambiguous: by the time it can fire, the only
        remaining way to reach it is a `UNIQUE` violation on `username`,
        which is exactly the property a typo must not be able to cost an
        invite.
        """
        async with self._sessionmaker() as db:
            try:
                async with db.begin():
                    db.add(
                        User(
                            id=user_id,
                            username=username,
                            password_hash=password_hash,
                            display_name=display_name,
                            role=str(UserRole.PLAYER),
                            is_active=True,
                        )
                    )
                    # Force the INSERT now, so a duplicate username raises
                    # here and nowhere else — that is what keeps the
                    # `IntegrityError` handler below unambiguous.
                    await db.flush()
                    claimed = await db.execute(
                        update(InviteCode)
                        .where(
                            InviteCode.code_hash == code_hash,
                            InviteCode.used_by.is_(None),
                            InviteCode.revoked_at.is_(None),
                            InviteCode.expires_at > now,
                        )
                        .values(used_by=user_id, used_at=now)
                        .returning(InviteCode.id)
                    )
                    if claimed.scalar_one_or_none() is None:
                        # Rolls the whole transaction back, user insert
                        # included.
                        raise _InviteUnavailable
            except _InviteUnavailable:
                return RedeemOutcome.INVITE_INVALID
            except IntegrityError:
                # `users.username` is UNIQUE. The transaction is already
                # rolled back, so the invite is untouched and claimable.
                return RedeemOutcome.USERNAME_TAKEN
        return RedeemOutcome.OK
