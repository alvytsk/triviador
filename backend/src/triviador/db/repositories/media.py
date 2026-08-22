"""`media_assets`, whose primary key is the content hash (Plan 3's model).

`ensure` is `INSERT ... ON CONFLICT DO NOTHING` followed by a read, not
`SELECT`-then-`INSERT`: the two-statement form races two concurrent
uploads of the same image into one `UniqueViolation`, and the losing
admin's upload — which succeeded in every way that matters, the blob is
written and identical — would fail.
"""

from sqlalchemy import delete, select, text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import IntegrityError
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

    async def unreferenced(self) -> tuple[MediaAssetRecord, ...]:
        """The read-only half: what *would* be collected. `--dry-run` and
        the tests use this; the sweep proper uses `claim_unreferenced`,
        which runs the same query with `FOR UPDATE` and deletes."""
        async with self._sessionmaker() as session:
            return await self._unreferenced(session, lock=False)

    @staticmethod
    async def _unreferenced(session: AsyncSession, *, lock: bool) -> tuple[MediaAssetRecord, ...]:
        """§10.4's two-way check, as one statement.

        The event half is a jsonpath scan: `$.**.media_asset_id` finds the
        field at any depth, which is what the snapshot nesting requires
        (question level and choice level, inside an array, inside `pool`).
        It is an unindexed sequential scan over `game_events`, and that is
        the right trade — `media-gc` is a rare command an operator runs,
        and an index maintained on every event append to serve it would be
        a cost paid by every game for a query nobody runs during play.

        `#>> '{}'` unwraps the jsonb scalar to text; a JSON `null`
        unwraps to SQL `NULL`, which the anti-join then ignores — exactly
        right, since a question with no media names no asset.
        """
        statement = text(
            """
            WITH referenced AS (
                SELECT DISTINCT jsonb_path_query(payload, '$.**.media_asset_id') #>> '{}' AS id
                FROM game_events
            )
            SELECT ma.id, ma.mime_type, ma.width, ma.height, ma.byte_size, ma.storage_key
            FROM media_assets ma
            WHERE NOT EXISTS (SELECT 1 FROM questions q WHERE q.media_asset_id = ma.id)
              -- `question_choices.media_asset_id` is reserved, not
              -- unreachable-by-design: no write path populates it today
              -- (`question_admin.py::_write_children` always inserts
              -- `NULL`, and the CSV importer supports one `media_file`
              -- per QUESTION, not per choice), so this branch never
              -- actually excludes a row right now. Kept anyway — it is
              -- the column's own half of §10.4's two-way check, and the
              -- day a per-choice image write path exists, an asset it
              -- references must not be swept as unreferenced.
              AND NOT EXISTS (
                    SELECT 1 FROM question_choices c WHERE c.media_asset_id = ma.id
              )
              AND NOT EXISTS (SELECT 1 FROM referenced r WHERE r.id = ma.id)
            ORDER BY ma.id
            """
            # `FOR UPDATE OF ma` locks only the `media_assets` rows this
            # returns — not `questions`, not `game_events`, both of which
            # this statement only reads.
            + (" FOR UPDATE OF ma" if lock else "")
        )
        rows = (await session.execute(statement)).all()
        return tuple(
            MediaAssetRecord(
                asset_id=row[0],
                mime_type=row[1],
                width=row[2],
                height=row[3],
                byte_size=row[4],
                storage_key=row[5],
            )
            for row in rows
        )

    async def claim_unreferenced(self) -> tuple[MediaAssetRecord, ...]:
        """Delete the rows, in one transaction, and hand them back so the
        caller can delete the objects.

        **Rows before objects, and the check repeated under the lock.**
        `SELECT ... FOR UPDATE` on each candidate row is what makes this
        safe against an admin attaching that asset to a question at the
        same moment: PostgreSQL takes `FOR KEY SHARE` on a parent row when
        a child row referencing it is inserted, and that conflicts with
        `FOR UPDATE`. So a question insert either happens before this
        transaction's `SELECT` (and the anti-join sees it, and the asset
        is spared) or blocks on the lock and commits only once this
        transaction has released it.

        **The per-row `SAVEPOINT` is why that second case is still safe.**
        `FOR UPDATE` locks the *row*, not the read: PostgreSQL only
        re-evaluates a locked row's own qualifying condition against a
        newer version of that same row (`EvalPlanQual`), it does not rerun
        a correlated `NOT EXISTS` against a sibling table just because a
        blocked transaction unblocked. So a question insert that commits
        while this one is waiting on the lock is invisible to the
        `SELECT`'s own verdict — the candidate list still calls the asset
        unreferenced. What is not fooled is the `DELETE` immediately
        after, in its own `SAVEPOINT`: it is a fresh statement in this
        transaction, sees the now-committed child row, and the foreign
        key refuses it — verified against real PostgreSQL by
        `tests/db/test_media_gc.py::
        test_a_question_attached_during_the_sweep_cannot_lose_its_asset`,
        which failed with an unhandled `IntegrityError` before this
        savepoint existed. Catching it here and moving on, rather than
        letting it abort the whole transaction, is what keeps one raced
        candidate from also losing every other row this sweep would
        otherwise have claimed.

        Deleting the row first also decides what a crash leaves behind: an
        object with no row, which the orphan pass collects on the next
        run. The opposite order leaves a row whose object is gone — a
        question that renders a broken image forever.
        """
        async with self._sessionmaker() as session, session.begin():
            candidates = await self._unreferenced(session, lock=True)
            claimed: list[MediaAssetRecord] = []
            for record in candidates:
                try:
                    async with session.begin_nested():
                        await session.execute(
                            delete(MediaAsset).where(MediaAsset.id == record.asset_id)
                        )
                except IntegrityError:
                    continue
                claimed.append(record)
            return tuple(claimed)

    async def all_storage_keys(self) -> frozenset[str]:
        """Every key the database believes in, for the orphan sweep: §10.3
        says a failed import transaction leaves an unreferenced blob and
        `media-gc` removes it safely, and a blob with no row is invisible
        to `unreferenced()`."""
        async with self._sessionmaker() as session:
            keys = (await session.execute(select(MediaAsset.storage_key))).scalars().all()
        return frozenset(keys)

    async def delete(self, asset_id: str) -> None:
        async with self._sessionmaker() as session, session.begin():
            await session.execute(delete(MediaAsset).where(MediaAsset.id == asset_id))
