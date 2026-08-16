import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="session")]


async def test_the_test_database_is_postgres_17_or_newer(engine: AsyncEngine) -> None:
    """SQLite is not a fallback: JSONB, partial unique indexes, FOR SHARE and
    TIMESTAMPTZ semantics are what the rest of this suite asserts."""
    async with engine.connect() as conn:
        version = (await conn.execute(text("SHOW server_version_num"))).scalar_one()
    assert int(version) >= 170000, f"expected PostgreSQL >= 17, got {version}"


async def test_the_sessions_fixture_shares_the_engine_loop(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    """`sessions` is a function-scoped fixture built from the session-scoped
    `engine`; it needs `loop_scope="session"` too, or it runs on a different
    loop than the connections it hands out. Exercised with a bare `SELECT 1`
    because the schema (Task 3) doesn't exist yet for `clean_db` to truncate."""
    async with sessions() as session:
        result = await session.execute(text("SELECT 1"))
        assert result.scalar_one() == 1
