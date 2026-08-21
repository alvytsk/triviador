"""§9.3's expiry, which is a state machine precisely because it cannot be
a transaction.

    validated --(expired by time, or by a restore)--> expired
    expired   --(staged object deleted)-----------> cleaned, staged_key = NULL
    confirmed --(staged object deleted)-----------> confirmed, staged_key = NULL

Every step is retryable, and a crash anywhere leaves a state the next run
resumes from. The tests below kill the process between each pair of steps.
"""

from datetime import UTC, datetime, timedelta

import pytest

from tests.api.fakes import FakeClock, FakeImports, FakeStagingStore
from triviador.imports.retire import ImportRetirer
from triviador.services.admin import ImportStatus

pytestmark = pytest.mark.asyncio

NOW = datetime(2026, 8, 20, 12, 0, tzinfo=UTC)


async def test_an_expired_validated_import_loses_its_staged_object() -> None:
    imports, staging = FakeImports(), FakeStagingStore()
    imports.add(
        "imp-1", status=ImportStatus.VALIDATED, staged_key="k", expires_at=NOW - timedelta(hours=1)
    )
    staging.objects["k"] = b"raw"
    await ImportRetirer(imports=imports, staging=staging, clock=FakeClock(NOW)).run()
    assert imports.records["imp-1"].status is ImportStatus.CLEANED
    assert imports.records["imp-1"].staged_key is None
    assert staging.objects == {}


async def test_an_unexpired_import_is_left_alone() -> None:
    imports, staging = FakeImports(), FakeStagingStore()
    imports.add(
        "imp-1", status=ImportStatus.VALIDATED, staged_key="k", expires_at=NOW + timedelta(hours=1)
    )
    staging.objects["k"] = b"raw"
    await ImportRetirer(imports=imports, staging=staging, clock=FakeClock(NOW)).run()
    assert imports.records["imp-1"].status is ImportStatus.VALIDATED
    assert staging.objects == {"k": b"raw"}


async def test_a_confirmed_import_keeps_its_row_as_an_audit_trail() -> None:
    imports, staging = FakeImports(), FakeStagingStore()
    imports.add("imp-1", status=ImportStatus.CONFIRMED, staged_key="k", expires_at=NOW)
    staging.objects["k"] = b"raw"
    await ImportRetirer(imports=imports, staging=staging, clock=FakeClock(NOW)).run()
    assert imports.records["imp-1"].status is ImportStatus.CONFIRMED
    assert imports.records["imp-1"].staged_key is None
    assert staging.objects == {}


async def test_a_crash_after_the_status_update_is_resumed_by_the_next_run() -> None:
    """The row says `expired` and the object is still there — the state a
    crash between step 1 and step 2 leaves."""
    imports, staging = FakeImports(), FakeStagingStore()
    imports.add(
        "imp-1", status=ImportStatus.EXPIRED, staged_key="k", expires_at=NOW - timedelta(days=2)
    )
    staging.objects["k"] = b"raw"
    await ImportRetirer(imports=imports, staging=staging, clock=FakeClock(NOW)).run()
    assert imports.records["imp-1"].status is ImportStatus.CLEANED
    assert staging.objects == {}


async def test_a_missing_object_still_reaches_cleaned() -> None:
    """A crash between the delete and the second update. Deleting an
    already-absent object is a no-op, so the run finishes the job."""
    imports, staging = FakeImports(), FakeStagingStore()
    imports.add("imp-1", status=ImportStatus.EXPIRED, staged_key="k", expires_at=NOW)
    await ImportRetirer(imports=imports, staging=staging, clock=FakeClock(NOW)).run()
    assert imports.records["imp-1"].status is ImportStatus.CLEANED


async def test_a_dry_run_expires_nothing_and_deletes_nothing() -> None:
    """`media-gc --dry-run` prints "nothing was deleted". Retirement is the
    destructive half — it removes the only copy of an upload an admin may
    still want to confirm — so it has to hear about the flag too."""
    imports, staging = FakeImports(), FakeStagingStore()
    imports.add(
        "imp-1", status=ImportStatus.VALIDATED, staged_key="k", expires_at=NOW - timedelta(hours=1)
    )
    staging.objects["k"] = b"raw"
    report = await ImportRetirer(imports=imports, staging=staging, clock=FakeClock(NOW)).run(
        dry_run=True
    )
    assert report.deleted is False
    assert report.expired == 1          # what it *would* have expired
    # What it *would* have deleted, too: "imp-1" is still `validated` here
    # (this is a dry run), so a real run's `mark_expired` step would flip
    # it to `expired` first and only then see its `staged_key` — undercounting
    # this to 0, as it used to, would tell an operator running `media-gc
    # --dry-run` that nothing would be deleted when one object actually would.
    assert report.objects_deleted == 1
    assert imports.records["imp-1"].status is ImportStatus.VALIDATED
    assert staging.objects == {"k": b"raw"}


async def test_after_a_restore_every_unconfirmed_import_is_expired() -> None:
    """§9.3: staging is deliberately not backed up (§10.9), so a `validated`
    row that survived the restore offers a confirm that cannot work."""
    imports, staging = FakeImports(), FakeStagingStore()
    imports.add(
        "imp-1", status=ImportStatus.VALIDATED, staged_key="k", expires_at=NOW + timedelta(days=7)
    )
    await ImportRetirer(imports=imports, staging=staging, clock=FakeClock(NOW)).run(
        after_restore=True
    )
    assert imports.records["imp-1"].status is ImportStatus.CLEANED
