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

from sqlalchemy import select
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

        Categories are created on the fly. The dry-run already reported
        how many rows carry each slug, so refusing an unknown one here
        would turn the first import of a new topic into a two-step dance
        for no safety.
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
        slugs = {row.category_slug for row in rows}
        existing = {
            row.slug: row.id
            for row in (
                await session.execute(select(Category).where(Category.slug.in_(slugs)))
            ).scalars()
        }
        for slug in sorted(slugs - set(existing)):
            category = Category(id=str(uuid4()), slug=slug, name=slug.replace("-", " ").title())
            session.add(category)
            existing[slug] = category.id
        await session.flush()
        return existing

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
