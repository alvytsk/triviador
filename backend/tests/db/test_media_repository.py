"""`pytestmark` per module, `loop_scope="session"` per async test — the
discipline `tests/db/conftest.py`'s docstring explains."""

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tests.db.conftest import _seed_user
from triviador.db.repositories.media import MediaAssetRepository

pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="session")]


async def test_ensure_is_idempotent_and_reports_which_call_created_the_row(
    sessions: async_sessionmaker[AsyncSession], clean_db: None
) -> None:
    await _seed_user(sessions, "admin-1")
    repository = MediaAssetRepository(sessions)
    first, created = await repository.ensure(
        asset_id="a" * 64,
        mime_type="image/webp",
        width=100,
        height=50,
        byte_size=1234,
        storage_key="aa/aaa.webp",
        created_by="admin-1",
    )
    second, created_again = await repository.ensure(
        asset_id="a" * 64,
        mime_type="image/webp",
        width=100,
        height=50,
        byte_size=1234,
        storage_key="aa/aaa.webp",
        created_by="admin-1",
    )
    assert created is True and created_again is False
    assert first == second
