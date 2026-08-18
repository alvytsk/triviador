import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from triviador.cli import AdminCreateOutcome, admin_create
from triviador.db.repositories.auth import UserRepository
from triviador.db.security import Argon2Hasher
from triviador.services.identity import UserRole

pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="session")]


async def create(sessions: async_sessionmaker[AsyncSession], **kw: object) -> AdminCreateOutcome:
    return await admin_create(
        users=UserRepository(sessions),
        hasher=Argon2Hasher(),
        username=str(kw.get("username", "root")),
        password=str(kw.get("password", "correct horse")),
        display_name=str(kw.get("display_name", "Root")),
        force=bool(kw.get("force", False)),
    )


async def test_with_no_admins_it_creates_one(
    clean_db: None, sessions: async_sessionmaker[AsyncSession]
) -> None:
    assert await create(sessions) == AdminCreateOutcome.CREATED
    user = await UserRepository(sessions).get_by_username("root")
    assert user is not None and user.role is UserRole.ADMIN


async def test_re_running_with_the_same_username_is_a_no_op(
    clean_db: None, sessions: async_sessionmaker[AsyncSession]
) -> None:
    """ "Safe in a deployment script" is the requirement: a provisioning
    step that runs on every boot must not fail on the second boot."""
    await create(sessions)
    assert await create(sessions) == AdminCreateOutcome.ALREADY_EXISTS


async def test_a_second_admin_is_refused_without_force(
    clean_db: None, sessions: async_sessionmaker[AsyncSession]
) -> None:
    await create(sessions, username="root")
    assert await create(sessions, username="other") == AdminCreateOutcome.REFUSED
    assert await UserRepository(sessions).get_by_username("other") is None


async def test_force_creates_the_second_admin(
    clean_db: None, sessions: async_sessionmaker[AsyncSession]
) -> None:
    await create(sessions, username="root")
    assert await create(sessions, username="other", force=True) == AdminCreateOutcome.CREATED


async def test_the_password_is_never_stored_in_the_clear(
    clean_db: None, sessions: async_sessionmaker[AsyncSession]
) -> None:
    await create(sessions, password="correct horse")
    user = await UserRepository(sessions).get_by_username("root")
    assert user is not None and "correct horse" not in user.password_hash
