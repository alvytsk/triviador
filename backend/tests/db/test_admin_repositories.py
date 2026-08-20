import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from triviador.db.repositories.categories import CategoryRepository
from triviador.services.admin import CategoryRecord, SlugTaken

pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="session")]


async def test_a_duplicate_slug_raises_slug_taken(
    clean_db: None, sessions: async_sessionmaker[AsyncSession]
) -> None:
    repository = CategoryRepository(sessions)
    await repository.create(slug="film", name="Film")
    with pytest.raises(SlugTaken):
        await repository.create(slug="film", name="Cinema")


async def test_rename_leaves_the_slug_alone(
    clean_db: None, sessions: async_sessionmaker[AsyncSession]
) -> None:
    repository = CategoryRepository(sessions)
    created = await repository.create(slug="sport", name="Sport")
    renamed = await repository.rename(created.category_id, name="Sports")
    assert renamed == CategoryRecord(created.category_id, "sport", "Sports")
