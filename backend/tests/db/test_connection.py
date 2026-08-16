import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

pytestmark = pytest.mark.integration


async def test_the_test_database_is_postgres_17_or_newer(engine: AsyncEngine) -> None:
    """SQLite is not a fallback: JSONB, partial unique indexes, FOR SHARE and
    TIMESTAMPTZ semantics are what the rest of this suite asserts."""
    async with engine.connect() as conn:
        version = (await conn.execute(text("SHOW server_version_num"))).scalar_one()
    assert int(version) >= 170000, f"expected PostgreSQL >= 17, got {version}"
