"""The four ways a session is dead, and the one way an invite is claimed.

pytestmark is the integration pair this directory requires — see
`tests/db/conftest.py` for why the loop scope is not optional.
"""

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from triviador.db.models.auth import InviteCode, User
from triviador.db.repositories.auth import (
    InviteRepository,
    SessionRepository,
    UserRepository,
)
from triviador.domain.ids import SessionId, UserId
from triviador.services.identity import RedeemOutcome, UserRole

pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="session")]

NOW = datetime(2026, 8, 18, 12, 0, tzinfo=UTC)
LATER = NOW + timedelta(days=1)


async def a_user(sessions: async_sessionmaker[AsyncSession], **kw: object) -> UserId:
    user_id = UserId(str(kw.get("user_id", "u1")))
    await UserRepository(sessions).create(
        user_id=user_id,
        username=str(kw.get("username", "player")),
        password_hash="hash",
        display_name="Player",
        role=UserRole(str(kw.get("role", "player"))),
    )
    return user_id


async def a_session(
    sessions: async_sessionmaker[AsyncSession], user_id: UserId, **kw: object
) -> SessionId:
    session_id = SessionId(str(kw.get("session_id", "s1")))
    await SessionRepository(sessions).create(
        session_id=session_id,
        user_id=user_id,
        token_hash=str(kw.get("token_hash", "digest")),
        expires_at=kw.get("expires_at", LATER),  # type: ignore[arg-type]
    )
    return session_id


async def test_a_live_session_resolves_to_its_principal(
    clean_db: None, sessions: async_sessionmaker[AsyncSession]
) -> None:
    user_id = await a_user(sessions, role="admin")
    session_id = await a_session(sessions, user_id)
    principal = await SessionRepository(sessions).resolve("digest", now=NOW)
    assert principal is not None
    assert (principal.user_id, principal.role, principal.session_id) == (
        user_id,
        UserRole.ADMIN,
        session_id,
    )


async def test_an_unknown_token_resolves_to_nothing(
    clean_db: None, sessions: async_sessionmaker[AsyncSession]
) -> None:
    assert await SessionRepository(sessions).resolve("nope", now=NOW) is None


async def test_an_expired_session_resolves_to_nothing(
    clean_db: None, sessions: async_sessionmaker[AsyncSession]
) -> None:
    user_id = await a_user(sessions)
    await a_session(sessions, user_id, expires_at=NOW - timedelta(seconds=1))
    assert await SessionRepository(sessions).resolve("digest", now=NOW) is None


async def test_a_revoked_session_resolves_to_nothing(
    clean_db: None, sessions: async_sessionmaker[AsyncSession]
) -> None:
    user_id = await a_user(sessions)
    session_id = await a_session(sessions, user_id)
    await SessionRepository(sessions).revoke(session_id, at=NOW)
    assert await SessionRepository(sessions).resolve("digest", now=NOW) is None


async def test_a_deactivated_users_session_resolves_to_nothing(
    clean_db: None, sessions: async_sessionmaker[AsyncSession]
) -> None:
    """Spec 1 §7's whole argument for a session table instead of a JWT."""
    user_id = await a_user(sessions)
    await a_session(sessions, user_id)
    async with sessions() as session, session.begin():
        user = await session.get(User, user_id)
        assert user is not None
        user.is_active = False
    assert await SessionRepository(sessions).resolve("digest", now=NOW) is None


async def test_revoking_a_user_closes_every_session_and_names_them(
    clean_db: None, sessions: async_sessionmaker[AsyncSession]
) -> None:
    user_id = await a_user(sessions)
    await a_session(sessions, user_id, session_id="s1", token_hash="d1")
    await a_session(sessions, user_id, session_id="s2", token_hash="d2")
    closed = await SessionRepository(sessions).revoke_for_user(user_id, at=NOW)
    assert set(closed) == {SessionId("s1"), SessionId("s2")}
    assert await SessionRepository(sessions).resolve("d1", now=NOW) is None
    assert await SessionRepository(sessions).resolve("d2", now=NOW) is None


async def test_revoking_one_session_leaves_the_users_other_tabs_alone(
    clean_db: None, sessions: async_sessionmaker[AsyncSession]
) -> None:
    user_id = await a_user(sessions)
    await a_session(sessions, user_id, session_id="s1", token_hash="d1")
    await a_session(sessions, user_id, session_id="s2", token_hash="d2")
    await SessionRepository(sessions).revoke(SessionId("s1"), at=NOW)
    assert await SessionRepository(sessions).resolve("d2", now=NOW) is not None


async def test_a_user_is_found_by_username_and_by_id(
    clean_db: None, sessions: async_sessionmaker[AsyncSession]
) -> None:
    user_id = await a_user(sessions, username="alice")
    repo = UserRepository(sessions)
    by_name = await repo.get_by_username("alice")
    by_id = await repo.get(user_id)
    assert by_name == by_id
    assert by_name is not None and by_name.password_hash == "hash"


async def test_admins_are_counted_and_players_are_not(
    clean_db: None, sessions: async_sessionmaker[AsyncSession]
) -> None:
    await a_user(sessions, user_id="u1", username="a", role="admin")
    await a_user(sessions, user_id="u2", username="b", role="player")
    assert await UserRepository(sessions).count_admins() == 1


async def seed_invite(sessions: async_sessionmaker[AsyncSession], **kw: object) -> None:
    async with sessions() as session, session.begin():
        session.add(
            InviteCode(
                id=str(kw.get("id", "i1")),
                code_hash=str(kw.get("code_hash", "chash")),
                created_by=str(kw.get("created_by", "admin")),
                expires_at=kw.get("expires_at", LATER),
                revoked_at=kw.get("revoked_at"),
            )
        )


async def redeem(sessions: async_sessionmaker[AsyncSession], **kw: object) -> RedeemOutcome:
    return await InviteRepository(sessions).redeem(
        code_hash=str(kw.get("code_hash", "chash")),
        user_id=UserId(str(kw.get("user_id", "new"))),
        username=str(kw.get("username", "newbie")),
        password_hash="hash",
        display_name="Newbie",
        now=NOW,
    )


async def test_redeeming_a_valid_invite_creates_the_user_and_marks_it_used(
    clean_db: None, sessions: async_sessionmaker[AsyncSession]
) -> None:
    await a_user(sessions, user_id="admin", username="admin", role="admin")
    await seed_invite(sessions)
    assert await redeem(sessions) == RedeemOutcome.OK
    created = await UserRepository(sessions).get_by_username("newbie")
    assert created is not None and created.role is UserRole.PLAYER
    async with sessions() as session:
        invite = await session.get(InviteCode, "i1")
        assert invite is not None and invite.used_by == "new"


async def test_a_second_redemption_of_one_invite_is_refused(
    clean_db: None, sessions: async_sessionmaker[AsyncSession]
) -> None:
    """The conditional UPDATE is the concurrency check as well as the
    business rule; this is its sequential half."""
    await a_user(sessions, user_id="admin", username="admin", role="admin")
    await seed_invite(sessions)
    assert await redeem(sessions) == RedeemOutcome.OK
    assert await redeem(sessions, user_id="other", username="other") == RedeemOutcome.INVITE_INVALID


@pytest.mark.parametrize(
    "invite",
    [
        {"expires_at": NOW - timedelta(seconds=1)},
        {"revoked_at": NOW},
        {"code_hash": "different"},
    ],
    ids=["expired", "revoked", "unknown"],
)
async def test_an_unusable_invite_is_refused(
    clean_db: None, sessions: async_sessionmaker[AsyncSession], invite: dict[str, object]
) -> None:
    await a_user(sessions, user_id="admin", username="admin", role="admin")
    await seed_invite(sessions, **invite)
    assert await redeem(sessions) == RedeemOutcome.INVITE_INVALID


async def test_a_taken_username_refuses_without_consuming_the_invite(
    clean_db: None, sessions: async_sessionmaker[AsyncSession]
) -> None:
    """The property the single-transaction design exists for: a redemption
    that fails on the username must leave the invite claimable, or a typo
    costs the invite."""
    await a_user(sessions, user_id="admin", username="taken", role="admin")
    await seed_invite(sessions)
    assert await redeem(sessions, username="taken") == RedeemOutcome.USERNAME_TAKEN
    async with sessions() as session:
        invite = await session.get(InviteCode, "i1")
        assert invite is not None and invite.used_by is None
    assert await redeem(sessions, username="fresh") == RedeemOutcome.OK


async def test_a_successful_redemption_leaves_exactly_one_new_user_and_a_used_invite(
    clean_db: None, sessions: async_sessionmaker[AsyncSession]
) -> None:
    """Insert-then-claim's happy path, checked against the database rather
    than against `redeem`'s own return value: exactly one `users` row for
    the new username, and the invite marked used by that same id."""
    await a_user(sessions, user_id="admin", username="admin", role="admin")
    await seed_invite(sessions)
    assert await redeem(sessions) == RedeemOutcome.OK
    async with sessions() as session:
        users = (
            (await session.execute(select(User).where(User.username == "newbie"))).scalars().all()
        )
        assert len(users) == 1 and users[0].id == "new"
        invite = await session.get(InviteCode, "i1")
        assert invite is not None and invite.used_by == "new" and invite.used_at is not None


async def test_a_lost_claim_leaves_no_orphan_user_row(
    clean_db: None, sessions: async_sessionmaker[AsyncSession]
) -> None:
    """The regression guard for insert-then-claim: a redemption that
    inserts its user but then loses the conditional UPDATE (because the
    invite was already claimed) must roll that insert back with the failed
    claim, or the loser of the race ends up with an account despite never
    holding a valid invite."""
    await a_user(sessions, user_id="admin", username="admin", role="admin")
    await seed_invite(sessions)
    assert await redeem(sessions) == RedeemOutcome.OK
    assert await redeem(sessions, user_id="other", username="other") == RedeemOutcome.INVITE_INVALID
    assert await UserRepository(sessions).get_by_username("other") is None
