"""`media_assets`, whose primary key is the content hash (Plan 3's model).

`ensure` is `INSERT ... ON CONFLICT DO NOTHING` followed by a read, not
`SELECT`-then-`INSERT`: the two-statement form races two concurrent
uploads of the same image into one `UniqueViolation`, and the losing
admin's upload — which succeeded in every way that matters, the blob is
written and identical — would fail.
"""

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from triviador.db.models.content import MediaAsset
from triviador.services.admin import MediaAssetRecord


def _to_record(row: MediaAsset) -> MediaAssetRecord:
    return MediaAssetRecord(
        asset_id=row.id,
        mime_type=row.mime_type,
        width=row.width,
        height=row.height,
        byte_size=row.byte_size,
        storage_key=row.storage_key,
    )


class MediaAssetRepository:
    """Implements `services.admin.MediaAssetPort`."""

    def __init__(self, sessionmaker: async_sessionmaker[AsyncSession]) -> None:
        self._sessionmaker = sessionmaker

    async def ensure(
        self,
        *,
        asset_id: str,
        mime_type: str,
        width: int,
        height: int,
        byte_size: int,
        storage_key: str,
        created_by: str,
    ) -> tuple[MediaAssetRecord, bool]:
        async with self._sessionmaker() as session, session.begin():
            inserted = await session.execute(
                insert(MediaAsset)
                .values(
                    id=asset_id,
                    mime_type=mime_type,
                    width=width,
                    height=height,
                    byte_size=byte_size,
                    storage_key=storage_key,
                    created_by=created_by,
                )
                .on_conflict_do_nothing(index_elements=[MediaAsset.id])
                .returning(MediaAsset)
            )
            row = inserted.scalar_one_or_none()
            if row is not None:
                return _to_record(row), True
            existing = await session.execute(select(MediaAsset).where(MediaAsset.id == asset_id))
            return _to_record(existing.scalar_one()), False

    async def get(self, asset_id: str) -> MediaAssetRecord | None:
        async with self._sessionmaker() as session:
            row = await session.get(MediaAsset, asset_id)
        return None if row is None else _to_record(row)
