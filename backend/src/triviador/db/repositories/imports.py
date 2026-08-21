"""`question_imports`: the only state that survives between the two phases.

The row is written at dry-run time and is the anchor for everything after
it — the confirm's `FOR UPDATE` (Task 8), the expiry machine's sweep
(Task 9), and the audit trail a confirmed import leaves behind. It is
therefore also the reason the row is written *before* the staged object:
an object with no row is invisible to all three.
"""

from collections.abc import Mapping, Sequence
from datetime import datetime
from typing import Any
from uuid import uuid4

from sqlalchemy import func, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from triviador.db.models.content import Category, MediaAsset, Question, QuestionImport
from triviador.db.repositories.question_admin import QuestionAdminRepository, _validate
from triviador.imports.digest import prompt_digest
from triviador.services.admin import (
    ImportedImage,
    ImportedQuestion,
    ImportRecord,
    ImportStatus,
    QuestionWrite,
)


def _to_record(row: QuestionImport) -> ImportRecord:
    return ImportRecord(
        import_id=row.id,
        uploaded_by=row.uploaded_by,
        upload_sha256=row.upload_sha256,
        filename=row.filename,
        staged_key=row.staged_key,
        row_count=row.row_count,
        rejected_count=row.rejected_count,
        report=row.report or {},
        status=ImportStatus(row.status),
        expires_at=row.expires_at,
    )


class QuestionImportRepository:
    """Implements `services.admin.ImportPort`."""

    def __init__(self, sessionmaker: async_sessionmaker[AsyncSession]) -> None:
        self._sessionmaker = sessionmaker

    async def create(
        self,
        *,
        import_id: str,
        uploaded_by: str,
        upload_sha256: str,
        filename: str,
        staged_key: str,
        row_count: int,
        rejected_count: int,
        report: dict[str, Any],
        expires_at: datetime,
    ) -> ImportRecord:
        row = QuestionImport(
            id=import_id,
            uploaded_by=uploaded_by,
            upload_sha256=upload_sha256,
            filename=filename,
            staged_key=staged_key,
            row_count=row_count,
            rejected_count=rejected_count,
            report=report,
            status=ImportStatus.VALIDATED.value,
            expires_at=expires_at,
        )
        async with self._sessionmaker() as session, session.begin():
            session.add(row)
        return _to_record(row)

    async def get(self, import_id: str) -> ImportRecord | None:
        async with self._sessionmaker() as session:
            row = await session.get(QuestionImport, import_id)
        return None if row is None else _to_record(row)

    async def count_expirable(self, now: datetime, *, all_unconfirmed: bool) -> int:
        """What `mark_expired` would touch. Read-only, for `--dry-run`."""
        async with self._sessionmaker() as session:
            statement = (
                select(func.count())
                .select_from(QuestionImport)
                .where(QuestionImport.status == ImportStatus.VALIDATED.value)
            )
            if not all_unconfirmed:
                statement = statement.where(QuestionImport.expires_at < now)
            return (await session.execute(statement)).scalar_one()

    async def mark_expired(self, now: datetime, *, all_unconfirmed: bool) -> int:
        async with self._sessionmaker() as session, session.begin():
            statement = (
                update(QuestionImport)
                .where(QuestionImport.status == ImportStatus.VALIDATED.value)
                .values(status=ImportStatus.EXPIRED.value)
                .returning(QuestionImport.id)
            )
            if not all_unconfirmed:
                statement = statement.where(QuestionImport.expires_at < now)
            return len((await session.execute(statement)).scalars().all())

    async def retirable_staged(self) -> tuple[tuple[str, str], ...]:
        async with self._sessionmaker() as session:
            rows = (
                await session.execute(
                    select(QuestionImport.id, QuestionImport.staged_key).where(
                        QuestionImport.staged_key.is_not(None),
                        QuestionImport.status.in_(
                            (ImportStatus.EXPIRED.value, ImportStatus.CONFIRMED.value)
                        ),
                    )
                )
            ).all()
        return tuple((row[0], row[1]) for row in rows)

    async def mark_cleaned(self, import_id: str) -> None:
        """`confirmed` stays `confirmed` — §9.3 keeps that row as an audit
        trail and only drops its `staged_key`. Only an `expired` row
        becomes `cleaned`."""
        async with self._sessionmaker() as session, session.begin():
            row = await session.get(QuestionImport, import_id)
            if row is None:
                return
            row.staged_key = None
            if row.status == ImportStatus.EXPIRED.value:
                row.status = ImportStatus.CLEANED.value

    async def apply_if_confirmable(
        self,
        import_id: str,
        *,
        rows: Sequence[ImportedQuestion],
        images: Mapping[str, ImportedImage],
        uploaded_by: str,
        now: datetime,
    ) -> bool:
        async with self._sessionmaker() as session, session.begin():
            row = await session.get(QuestionImport, import_id, with_for_update=True)
            if row is None:
                return False
            # All three conditions re-checked *under the lock*, not before
            # it: the values the route read a moment ago are exactly the
            # ones a concurrent confirm — or the clock — is about to
            # change. `expires_at` belongs here as much as `status` does;
            # without it a validated import stays confirmable forever and
            # §9.3's TTL only takes effect if `media-gc` happens to run.
            if row.status != ImportStatus.VALIDATED.value:
                return False
            if row.rejected_count != 0:
                return False
            if row.expires_at <= now:
                return False
            await self._write_bank(session, rows, images, uploaded_by)
            row.status = ImportStatus.CONFIRMED.value
            row.confirmed_at = now
            return True

    async def _write_bank(
        self,
        session: AsyncSession,
        rows: Sequence[ImportedQuestion],
        images: Mapping[str, ImportedImage],
        uploaded_by: str,
    ) -> None:
        """Every insert uses the session it is handed, never
        `self._sessionmaker`: opening a second session would put these
        writes in a second transaction, and §10.3's "no partial writes"
        would then mean "no partial writes unless the process dies between
        two of them".

        Categories are created on the fly. `imports/parse.py::_parse_row`
        already rejects a slug that does not match `CATEGORY_SLUG_PATTERN`
        before a row ever reaches here (Important #2 of the Plan 7A
        review — the comment this one replaces claimed the dry-run
        reported per-category information it never did), so every slug
        this creates is already the shape `CreateCategoryRequest` would
        accept from the interactive route. Refusing an unknown *category*
        a second time here would still turn the first import of a new
        topic into a two-step dance, for no safety a shape check upstream
        does not already give.
        """
        categories = await self._ensure_categories(session, rows)
        await self._ensure_assets(session, images, uploaded_by)
        for row in rows:
            write = QuestionWrite(
                kind=row.kind,
                prompt=row.prompt,
                category_id=categories[row.category_slug],
                difficulty=row.difficulty,
                media_asset_id=(
                    images[row.media_file].asset_id if row.media_file else None
                ),
                choices=row.choices,
                numeric_answer=row.numeric_answer,
                unit=row.unit,
            )
            # The same shape rule the hand-editor obeys, from the same
            # function — an importer with its own copy is an importer that
            # will one day accept three choices.
            _validate(write)
            question_id = str(uuid4())
            session.add(
                Question(
                    id=question_id,
                    version=1,
                    kind=write.kind,
                    prompt=write.prompt,
                    category_id=write.category_id,
                    difficulty=write.difficulty,
                    media_asset_id=write.media_asset_id,
                    is_active=True,
                    prompt_hash=prompt_digest(write.prompt),
                )
            )
            await session.flush()
            QuestionAdminRepository._write_children(session, question_id, write)

    @staticmethod
    async def _ensure_categories(
        session: AsyncSession, rows: Sequence[ImportedQuestion]
    ) -> dict[str, str]:
        """Every slug this import needs, created if new — `ON CONFLICT
        (slug) DO NOTHING`, matching `_ensure_assets` below.

        Was `SELECT`-then-`INSERT`: two concurrent confirms that both
        introduce the same new slug for the first time would both see it
        missing, both try to create it, and the loser would hit a raw
        `UniqueViolation` — the same spurious 503 Important #1 fixes for
        `questions`' foreign keys, on `categories.slug`'s UNIQUE
        constraint instead. The upsert removes the race outright rather
        than narrowing the window; the final `SELECT` is what makes the
        *winner's* id (not necessarily the id generated in this call) the
        one every row below actually gets, since the loser's locally
        generated `Category` never becomes a row.
        """
        slugs = {row.category_slug for row in rows}
        if not slugs:
            return {}
        for slug in slugs:
            await session.execute(
                insert(Category)
                .values(id=str(uuid4()), slug=slug, name=slug.replace("-", " ").title())
                .on_conflict_do_nothing(index_elements=[Category.slug])
            )
        await session.flush()
        rows_by_slug = (
            await session.execute(select(Category).where(Category.slug.in_(slugs)))
        ).scalars()
        return {row.slug: row.id for row in rows_by_slug}

    @staticmethod
    async def _ensure_assets(
        session: AsyncSession, images: Mapping[str, ImportedImage], uploaded_by: str
    ) -> None:
        for image in images.values():
            # `ON CONFLICT DO NOTHING`, because the same picture may
            # already be in the bank from an earlier import — content
            # addressing makes that the *same* asset, not a collision.
            await session.execute(
                insert(MediaAsset)
                .values(
                    id=image.asset_id,
                    mime_type=image.mime_type,
                    width=image.width,
                    height=image.height,
                    byte_size=image.byte_size,
                    storage_key=image.storage_key,
                    created_by=uploaded_by,
                )
                .on_conflict_do_nothing(index_elements=[MediaAsset.id])
            )
        await session.flush()
