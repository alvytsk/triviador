from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tests.db.conftest import _seed_user
from triviador.db.repositories.categories import CategoryRepository
from triviador.db.repositories.imports import QuestionImportRepository
from triviador.services.admin import CategoryRecord, ImportStatus, SlugTaken

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


async def test_two_concurrent_confirms_cannot_both_apply(
    sessions: async_sessionmaker[AsyncSession], clean_db: None
) -> None:
    """§9.3: "the second loses at `FOR UPDATE` and returns 409". Asserted
    against real PostgreSQL, because the property is the lock's, not the
    code's."""
    import asyncio

    await _seed_user(sessions, "admin-1")
    repository = QuestionImportRepository(sessions)
    record = await repository.create(
        import_id="imp-1",
        uploaded_by="admin-1",
        upload_sha256="sha",
        filename="b.csv",
        staged_key="imp-1/b.csv",
        row_count=1,
        rejected_count=0,
        report={"rejections": []},
        expires_at=datetime.now(UTC) + timedelta(hours=1),
    )
    assert record.status is ImportStatus.VALIDATED

    async def apply() -> bool:
        return await repository.apply_if_confirmable(
            "imp-1",
            rows=(),
            images={},
            uploaded_by="admin-1",
            now=datetime.now(UTC),
        )

    first, second = await asyncio.gather(apply(), apply())
    assert sorted([first, second]) == [False, True]


async def test_an_expired_import_cannot_be_applied_even_with_zero_rejections(
    sessions: async_sessionmaker[AsyncSession], clean_db: None
) -> None:
    """The check that belongs under the lock, not only in the route: an
    import whose TTL passed while the confirm was in flight must lose."""
    await _seed_user(sessions, "admin-1")
    repository = QuestionImportRepository(sessions)
    await repository.create(
        import_id="imp-2",
        uploaded_by="admin-1",
        upload_sha256="sha",
        filename="b.csv",
        staged_key="imp-2/b.csv",
        row_count=1,
        rejected_count=0,
        report={"rejections": [], "notices": []},
        expires_at=datetime.now(UTC) - timedelta(seconds=1),
    )
    assert not await repository.apply_if_confirmable(
        "imp-2", rows=(), images={}, uploaded_by="admin-1", now=datetime.now(UTC)
    )
