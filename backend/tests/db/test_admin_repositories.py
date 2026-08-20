from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tests.db.conftest import _seed_user
from triviador.db.repositories.categories import CategoryRepository
from triviador.db.repositories.imports import QuestionImportRepository
from triviador.services.admin import (
    CategoryRecord,
    ImportedImage,
    ImportedQuestion,
    ImportStatus,
    SlugTaken,
)

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


async def test_confirm_writes_every_kind_of_row_in_one_transaction(
    sessions: async_sessionmaker[AsyncSession], clean_db: None
) -> None:
    """The composition this task actually adds, against the real schema.

    Every individual statement here is proven elsewhere — questions and
    their children in `test_question_admin.py`, the media upsert in
    `test_media_repository.py`, category-ensure-in-the-caller's-transaction
    in `test_seed_questions.py`. What has never run against PostgreSQL is
    all five landing *together* inside the locked transaction with the
    status flip. A column-name typo, an FK ordering mistake or a
    `Decimal`/`NUMERIC` mismatch in that composition would pass every fake
    and fail on the first real import.
    """
    from sqlalchemy import text as sql

    await _seed_user(sessions, "admin-1")
    repository = QuestionImportRepository(sessions)
    await repository.create(
        import_id="imp-write",
        uploaded_by="admin-1",
        upload_sha256="sha",
        filename="bank.zip",
        staged_key="imp-write/bank.zip",
        row_count=2,
        rejected_count=0,
        report={"rejections": [], "notices": []},
        expires_at=datetime.now(UTC) + timedelta(hours=1),
    )

    applied = await repository.apply_if_confirmable(
        "imp-write",
        rows=(
            ImportedQuestion(
                category_slug="geography",
                kind="multiple_choice",
                prompt="Which river runs through Prague?",
                difficulty="easy",
                media_file="river.png",
                choices=(("Vltava", True), ("Elbe", False), ("Morava", False), ("Ohře", False)),
                numeric_answer=None,
                unit=None,
            ),
            ImportedQuestion(
                category_slug="history",
                kind="numeric",
                prompt="In which year did the Velvet Revolution begin?",
                difficulty="easy",
                media_file=None,
                choices=None,
                numeric_answer=Decimal("1989"),
                unit=None,
            ),
        ),
        images={
            "river.png": ImportedImage(
                asset_id="c" * 64,
                mime_type="image/webp",
                width=800,
                height=400,
                byte_size=1234,
                storage_key="cc/ccc.webp",
            )
        },
        uploaded_by="admin-1",
        now=datetime.now(UTC),
    )
    assert applied is True

    async with sessions() as session:
        counts = {
            table: (await session.execute(sql(f"SELECT count(*) FROM {table}"))).scalar_one()
            for table in (
                "categories",
                "questions",
                "question_choices",
                "question_numeric",
                "media_assets",
            )
        }
        status = (
            await session.execute(
                sql("SELECT status FROM question_imports WHERE id = 'imp-write'")
            )
        ).scalar_one()
        answer = (
            await session.execute(sql("SELECT correct_value FROM question_numeric"))
        ).scalar_one()
        attached = (
            await session.execute(
                sql("SELECT media_asset_id FROM questions WHERE kind = 'multiple_choice'")
            )
        ).scalar_one()

    assert counts == {
        "categories": 2,
        "questions": 2,
        "question_choices": 4,
        "question_numeric": 1,
        "media_assets": 1,
    }
    assert status == "confirmed"
    assert answer == Decimal("1989")
    assert attached == "c" * 64


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


async def _seed_import(
    repository: QuestionImportRepository,
    import_id: str,
    *,
    expires_at: datetime,
    staged_key: str | None = None,
) -> None:
    await repository.create(
        import_id=import_id,
        uploaded_by="admin-1",
        upload_sha256="sha",
        filename="b.csv",
        staged_key=staged_key or f"{import_id}/b.csv",
        row_count=1,
        rejected_count=0,
        report={"rejections": [], "notices": []},
        expires_at=expires_at,
    )


async def test_count_expirable_and_mark_expired_only_touch_validated_rows_past_their_ttl(
    sessions: async_sessionmaker[AsyncSession], clean_db: None
) -> None:
    """`imports/retire.py`'s SQL, against the real schema: neither method
    is exercised anywhere else — `test_retire.py` proves the state machine
    over `FakeImports`, never the `UPDATE ... RETURNING` this repository
    actually runs."""
    await _seed_user(sessions, "admin-1")
    now = datetime.now(UTC)
    repository = QuestionImportRepository(sessions)
    await _seed_import(repository, "imp-past", expires_at=now - timedelta(hours=1))
    await _seed_import(repository, "imp-future", expires_at=now + timedelta(hours=1))
    await repository.create(
        import_id="imp-confirmed",
        uploaded_by="admin-1",
        upload_sha256="sha",
        filename="c.csv",
        staged_key="imp-confirmed/c.csv",
        row_count=1,
        rejected_count=0,
        report={"rejections": [], "notices": []},
        expires_at=now + timedelta(hours=1),
    )
    assert await repository.apply_if_confirmable(
        "imp-confirmed", rows=(), images={}, uploaded_by="admin-1", now=now
    ) is True

    assert await repository.count_expirable(now, all_unconfirmed=False) == 1
    assert await repository.mark_expired(now, all_unconfirmed=False) == 1

    past = await repository.get("imp-past")
    future = await repository.get("imp-future")
    confirmed = await repository.get("imp-confirmed")
    assert past is not None and past.status is ImportStatus.EXPIRED
    assert future is not None and future.status is ImportStatus.VALIDATED
    assert confirmed is not None and confirmed.status is ImportStatus.CONFIRMED


async def test_all_unconfirmed_expires_every_validated_row_regardless_of_ttl(
    sessions: async_sessionmaker[AsyncSession], clean_db: None
) -> None:
    """`--after-restore`: staging is not backed up (§10.9), so every
    `validated` row survives a restore already unconfirmable, whatever its
    own `expires_at` says."""
    await _seed_user(sessions, "admin-1")
    now = datetime.now(UTC)
    repository = QuestionImportRepository(sessions)
    await _seed_import(repository, "imp-past", expires_at=now - timedelta(hours=1))
    await _seed_import(repository, "imp-future", expires_at=now + timedelta(days=7))

    assert await repository.count_expirable(now, all_unconfirmed=True) == 2
    assert await repository.mark_expired(now, all_unconfirmed=True) == 2

    past = await repository.get("imp-past")
    future = await repository.get("imp-future")
    assert past is not None and past.status is ImportStatus.EXPIRED
    assert future is not None and future.status is ImportStatus.EXPIRED


async def test_retirable_staged_returns_expired_and_confirmed_rows_with_a_staged_key(
    sessions: async_sessionmaker[AsyncSession], clean_db: None
) -> None:
    """Neither a still-`validated` row nor an already-`cleaned` one (its
    `staged_key` already `NULL`) may show up here — that column is what
    makes the whole machine idempotent."""
    await _seed_user(sessions, "admin-1")
    now = datetime.now(UTC)
    repository = QuestionImportRepository(sessions)
    await _seed_import(repository, "imp-validated", expires_at=now + timedelta(hours=1))
    await _seed_import(repository, "imp-expired", expires_at=now - timedelta(hours=1))
    await repository.create(
        import_id="imp-confirmed",
        uploaded_by="admin-1",
        upload_sha256="sha",
        filename="c.csv",
        staged_key="imp-confirmed/c.csv",
        row_count=1,
        rejected_count=0,
        report={"rejections": [], "notices": []},
        expires_at=now + timedelta(hours=1),
    )
    assert await repository.apply_if_confirmable(
        "imp-confirmed", rows=(), images={}, uploaded_by="admin-1", now=now
    ) is True
    assert await repository.mark_expired(now, all_unconfirmed=False) == 1
    await repository.mark_cleaned("imp-expired")
    cleaned = await repository.get("imp-expired")
    assert cleaned is not None and cleaned.status is ImportStatus.CLEANED

    staged = dict(await repository.retirable_staged())
    assert staged == {"imp-confirmed": "imp-confirmed/c.csv"}


async def test_mark_cleaned_only_flips_expired_to_cleaned_and_always_clears_staged_key(
    sessions: async_sessionmaker[AsyncSession], clean_db: None
) -> None:
    """§9.3's third step: `confirmed` stays `confirmed` — that row is the
    audit trail — but loses its `staged_key` all the same."""
    await _seed_user(sessions, "admin-1")
    now = datetime.now(UTC)
    repository = QuestionImportRepository(sessions)
    await _seed_import(repository, "imp-expired", expires_at=now - timedelta(hours=1))
    await repository.mark_expired(now, all_unconfirmed=False)
    await repository.create(
        import_id="imp-confirmed",
        uploaded_by="admin-1",
        upload_sha256="sha",
        filename="c.csv",
        staged_key="imp-confirmed/c.csv",
        row_count=1,
        rejected_count=0,
        report={"rejections": [], "notices": []},
        expires_at=now + timedelta(hours=1),
    )
    assert await repository.apply_if_confirmable(
        "imp-confirmed", rows=(), images={}, uploaded_by="admin-1", now=now
    ) is True

    await repository.mark_cleaned("imp-expired")
    await repository.mark_cleaned("imp-confirmed")
    await repository.mark_cleaned("no-such-row")  # a no-op, not an error

    expired = await repository.get("imp-expired")
    confirmed = await repository.get("imp-confirmed")
    assert expired is not None
    assert expired.status is ImportStatus.CLEANED
    assert expired.staged_key is None
    assert confirmed is not None
    assert confirmed.status is ImportStatus.CONFIRMED
    assert confirmed.staged_key is None
