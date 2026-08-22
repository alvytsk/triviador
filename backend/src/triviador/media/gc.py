"""§10.4's asset sweep. Two passes, and the ordering each one needs.

**Recorded assets: rows first, objects second.** `claim_unreferenced`
deletes the rows inside one transaction that holds `FOR UPDATE` on each
of them and re-checks the references under that lock (see its docstring —
the lock is what a concurrent question insert collides with). Only then
are the objects deleted. A crash in between leaves an object with no row,
which the orphan pass collects next time; the opposite order would leave
a question rendering a blob that is gone.

**Orphans: old ones only.** §10.3 says "a failed transaction leaves an
unreferenced blob, which `media-gc` removes safely" — but an object with
no row is *also* what an upload looks like for the few milliseconds
between its `put` and its `INSERT`. Age is the only thing that tells the
two apart, so anything younger than the grace period is left alone. The
upload path's `repair_blob` covers the residue (Decision 9).

**`--dry-run` mutates nothing at all.** Not the objects, not the rows,
and — in `cli.py` — not the import retirement either.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta

from triviador.services.admin import MediaAssetPort
from triviador.services.storage import MediaStore


@dataclass(frozen=True)
class GcReport:
    unreferenced: tuple[str, ...]
    orphan_objects: tuple[str, ...]
    skipped_young: int
    deleted: bool


class MediaCollector:
    def __init__(self, *, assets: MediaAssetPort, store: MediaStore, grace: timedelta) -> None:
        self._assets = assets
        self._store = store
        self._grace = grace

    async def run(self, *, now: datetime, dry_run: bool = False) -> GcReport:
        # Listed *before* anything is deleted, so an asset collected by
        # this run is not also reported as an orphan by it.
        listed = await self._store.list_objects()
        known = await self._assets.all_storage_keys()
        cutoff = now - self._grace
        candidates = [o for o in listed if o.key not in known]
        orphans = tuple(sorted(o.key for o in candidates if o.last_modified <= cutoff))
        skipped = len(candidates) - len(orphans)

        if dry_run:
            return GcReport(
                unreferenced=tuple(a.asset_id for a in await self._assets.unreferenced()),
                orphan_objects=orphans,
                skipped_young=skipped,
                deleted=False,
            )

        claimed = await self._assets.claim_unreferenced()
        for asset in claimed:
            await self._store.delete(asset.storage_key)
        for key in orphans:
            await self._store.delete(key)

        return GcReport(
            unreferenced=tuple(a.asset_id for a in claimed),
            orphan_objects=orphans,
            skipped_young=skipped,
            deleted=True,
        )
