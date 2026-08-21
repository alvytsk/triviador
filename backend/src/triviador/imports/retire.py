"""The expiry half of §9.3.

**Why the order is fixed.** PostgreSQL and Garage share no transaction, so
"delete the row and the object together" is not available. Deleting the
row first strands an untracked raw upload — full of correct answers — in
the staging bucket, with nothing left to find it by. Deleting the object
first leaves a row that still looks confirmable but whose upload is gone.
So the row is *first marked unconfirmable*, then the object goes, then the
row records that it went.
"""

from dataclasses import dataclass

from triviador.services.admin import ImportPort
from triviador.services.ports import Clock
from triviador.services.storage import ImportStagingStore


@dataclass(frozen=True)
class RetireReport:
    expired: int
    objects_deleted: int
    rows_cleaned: int
    deleted: bool


class ImportRetirer:
    def __init__(
        self, *, imports: ImportPort, staging: ImportStagingStore, clock: Clock
    ) -> None:
        self._imports = imports
        self._staging = staging
        self._clock = clock

    async def run(self, *, after_restore: bool = False, dry_run: bool = False) -> RetireReport:
        """`dry_run` reaches here too.

        Not obvious, and worth the parameter: `media-gc --dry-run` prints
        "nothing was deleted", and a retirement that expired rows and
        deleted staged uploads anyway would make that line a lie about the
        most destructive half of the command — the half that removes the
        only copy of an upload an admin may still want to confirm.
        """
        now = self._clock.now()
        if dry_run:
            would_expire = await self._imports.count_expirable(
                now, all_unconfirmed=after_restore
            )
            return RetireReport(
                expired=would_expire,
                # Not `len(await self._imports.retirable_staged())`: that
                # only sees rows already `expired`/`confirmed`. A real run
                # would first flip every row `would_expire` counts out of
                # `validated`, and only then would `retirable_staged()`
                # see it — `--dry-run` has to report that same blast
                # radius *before* running `mark_expired`, or it undercounts
                # exactly the rows this call just promised to expire.
                objects_deleted=await self._imports.expirable_staged_count(
                    now, all_unconfirmed=after_restore
                ),
                rows_cleaned=0,
                deleted=False,
            )

        expired = await self._imports.mark_expired(now, all_unconfirmed=after_restore)
        deleted = 0
        cleaned = 0
        # Every row that still owns a staged object, whatever put it in
        # that state: expired just now, expired by an earlier run that
        # crashed, or confirmed and no longer needing its upload.
        for import_id, staged_key in await self._imports.retirable_staged():
            await self._staging.delete(staged_key)
            deleted += 1
            await self._imports.mark_cleaned(import_id)
            cleaned += 1
        return RetireReport(
            expired=expired, objects_deleted=deleted, rows_cleaned=cleaned, deleted=True
        )
