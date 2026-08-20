"""`question_imports`: the only state that survives between the two phases.

The row is written at dry-run time and is the anchor for everything after
it — the confirm's `FOR UPDATE` (Task 8), the expiry machine's sweep
(Task 9), and the audit trail a confirmed import leaves behind. It is
therefore also the reason the row is written *before* the staged object:
an object with no row is invisible to all three.
"""

from datetime import datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from triviador.db.models.content import QuestionImport
from triviador.services.admin import ImportRecord, ImportStatus


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
