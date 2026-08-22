from dataclasses import replace as dc_replace
from datetime import timedelta

import httpx
import pytest_asyncio

from tests.api.conftest import ORIGIN
from tests.api.fakes import (
    FakeCategories,
    FakeClock,
    FakeMediaStore,
    FakeQuestionAdmin,
    FakeStagingStore,
)
from tests.imports.test_parse import MC, NUM, csv_bytes, zip_bytes
from tests.media.test_pipeline import png
from triviador.api.app import create_app
from triviador.api.deps import AppDependencies
from triviador.config import Settings
from triviador.media.pipeline import ImageNormalizer

# No module-level `pytestmark = pytest.mark.asyncio`: `asyncio_mode = "auto"`
# (pyproject.toml) already collects every `async def test_*` here without
# it, and this file also has a sync test
# (`test_confirmable_is_false_once_the_upload_expires`) that the mark would
# otherwise land on too, which pytest-asyncio warns about on every run.


@pytest_asyncio.fixture
async def deps(deps: AppDependencies) -> AppDependencies:
    """Overrides `conftest.py`'s `deps`, which seeds one pre-existing
    question (`q1`) for the admin-questions CRUD suite. The confirm tests
    below assert on the bank's exact contents and count after an import —
    `q1` would be indistinguishable noise in both, so this module starts
    every test from an empty bank instead. `categories`/`media_assets` stay
    untouched: nothing here seeds them, so they are already empty.
    """
    assert isinstance(deps.questions_admin, FakeQuestionAdmin)
    deps.questions_admin.records.clear()
    return deps


async def dry_run(client: httpx.AsyncClient, body: bytes, filename: str) -> httpx.Response:
    return await client.post(
        "/api/admin/questions/import/dry-run",
        content=body,
        headers={"Content-Type": "application/octet-stream", "X-Filename": filename},
    )


async def test_a_player_cannot_dry_run(signed_in: httpx.AsyncClient) -> None:
    assert (await dry_run(signed_in, csv_bytes(NUM), "b.csv")).status_code == 403


async def test_a_clean_upload_reports_zero_rejections_and_stages_the_file(
    admin_client: httpx.AsyncClient, deps: AppDependencies
) -> None:
    response = await dry_run(admin_client, csv_bytes(MC, NUM), "bank.csv")
    assert response.status_code == 201
    body = response.json()
    assert (body["row_count"], body["rejected_count"]) == (2, 0)
    assert body["status"] == "validated"
    assert body["confirmable"] is True
    assert isinstance(deps.staging_store, FakeStagingStore)
    assert deps.staging_store.objects[body["staged_key"]] == csv_bytes(MC, NUM)


async def test_nothing_is_written_to_the_bank(
    admin_client: httpx.AsyncClient, deps: AppDependencies
) -> None:
    """§9.3's dry-run invariant, as one assertion: no question, no
    category, no media asset, no public object."""
    assert isinstance(deps.questions_admin, FakeQuestionAdmin)
    assert isinstance(deps.media_store, FakeMediaStore)
    assert isinstance(deps.categories, FakeCategories)
    before = len(deps.questions_admin.records), len(deps.media_store.objects)
    await dry_run(
        admin_client,
        zip_bytes(csv_bytes(MC.replace(",,,", ",,,river.png")), {"river.png": png(32, 32)}),
        "bank.zip",
    )
    assert (len(deps.questions_admin.records), len(deps.media_store.objects)) == before
    assert deps.categories.records == {}


async def test_a_rejected_row_makes_the_upload_unconfirmable(
    admin_client: httpx.AsyncClient,
) -> None:
    """§10.3: CONFIRM is enabled only when `rejected == 0`. The server says
    so on the report rather than leaving the rule to the client."""
    response = await dry_run(
        admin_client, csv_bytes(MC, "numeric,No answer,history,easy,,,,,,,,"), "b.csv"
    )
    body = response.json()
    assert (body["row_count"], body["rejected_count"]) == (1, 1)
    assert body["confirmable"] is False


async def test_media_is_validated_during_the_dry_run(admin_client: httpx.AsyncClient) -> None:
    """A row whose image cannot be re-encoded must be rejected *now*.
    Otherwise `rejected == 0` would promise a confirm that fails halfway
    through, which is exactly the partial-write §10.3 forbids."""
    body = zip_bytes(csv_bytes(MC.replace(",,,", ",,,broken.png")), {"broken.png": b"not a png"})
    response = await dry_run(admin_client, body, "bank.zip")
    assert response.json()["rejected_count"] == 1
    assert "broken.png" in response.json()["rejections"][0]["reason"]


async def test_the_rejected_rows_come_back_as_csv(admin_client: httpx.AsyncClient) -> None:
    body = csv_bytes(MC, "numeric,No answer,history,easy,,,,,,,,")
    created = (await dry_run(admin_client, body, "b.csv")).json()
    response = await admin_client.get(
        f"/api/admin/questions/import/{created['import_id']}/rejected.csv"
    )
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/csv")
    lines = response.text.strip().splitlines()
    assert lines[0].endswith(",reason")
    assert "No answer" in lines[1]


async def test_a_bad_header_fails_the_whole_request(admin_client: httpx.AsyncClient) -> None:
    response = await dry_run(admin_client, b"a,b\n1,2", "b.csv")
    assert response.status_code == 422
    assert response.json()["code"] == "validation_failed"


async def test_an_upload_over_the_import_cap_is_refused(
    admin_client: httpx.AsyncClient, deps: AppDependencies
) -> None:
    oversized = b"x" * (deps.settings.import_max_bytes + 1)
    assert (await dry_run(admin_client, oversized, "b.csv")).status_code == 413


async def confirm(client: httpx.AsyncClient, import_id: str) -> httpx.Response:
    return await client.post(f"/api/admin/questions/import/{import_id}/confirm")


async def test_confirm_writes_every_row_once(
    admin_client: httpx.AsyncClient, deps: AppDependencies
) -> None:
    assert isinstance(deps.questions_admin, FakeQuestionAdmin)
    created = (await dry_run(admin_client, csv_bytes(MC, NUM), "bank.csv")).json()
    response = await confirm(admin_client, created["import_id"])
    assert response.status_code == 200
    assert response.json()["status"] == "confirmed"
    assert len(deps.questions_admin.records) == 2


async def test_a_second_confirm_is_409(admin_client: httpx.AsyncClient) -> None:
    """The row is `confirmed` and can never be applied again — which is
    what makes the button safe to double-click."""
    created = (await dry_run(admin_client, csv_bytes(NUM), "b.csv")).json()
    assert (await confirm(admin_client, created["import_id"])).status_code == 200
    second = await confirm(admin_client, created["import_id"])
    assert second.status_code == 409
    assert second.json()["code"] == "import_not_confirmable"


async def test_an_upload_with_rejections_cannot_be_confirmed(
    admin_client: httpx.AsyncClient, deps: AppDependencies
) -> None:
    assert isinstance(deps.questions_admin, FakeQuestionAdmin)
    created = (
        await dry_run(
            admin_client, csv_bytes(MC, "numeric,No answer,history,easy,,,,,,,,"), "b.csv"
        )
    ).json()
    response = await confirm(admin_client, created["import_id"])
    assert response.status_code == 409
    assert deps.questions_admin.records == {}


async def test_a_staged_object_that_changed_underneath_is_refused(
    admin_client: httpx.AsyncClient, deps: AppDependencies
) -> None:
    """The comparison §9.3 specifies: recomputed-from-staged against
    dry-run-stored. Nothing here trusts a sha the client sent."""
    assert isinstance(deps.staging_store, FakeStagingStore)
    created = (await dry_run(admin_client, csv_bytes(NUM), "b.csv")).json()
    deps.staging_store.objects[created["staged_key"]] = csv_bytes(MC)
    response = await confirm(admin_client, created["import_id"])
    assert response.status_code == 409
    assert "changed" in response.json()["message"]


async def test_an_expired_import_cannot_be_confirmed(
    admin_client: httpx.AsyncClient, deps: AppDependencies
) -> None:
    """§9.3 gives a staged upload a TTL. Without this check a validated
    import stays confirmable forever, and the TTL only bites if an
    operator happens to run `media-gc` first — which is a rule enforced by
    a cron job that does not exist yet."""
    assert isinstance(deps.clock, FakeClock)
    created = (await dry_run(admin_client, csv_bytes(NUM), "b.csv")).json()
    deps.clock.advance(timedelta(hours=deps.settings.import_ttl_hours + 1))
    response = await confirm(admin_client, created["import_id"])
    assert response.status_code == 409
    assert response.json()["code"] == "import_not_confirmable"
    assert "expired" in response.json()["message"]


def test_confirmable_is_false_once_the_upload_expires() -> None:
    """`_summary` is a pure function and is tested as one — §6.1 defines
    three import routes and no "read one import", so there is nowhere to
    observe this through HTTP without inventing a fourth.

    The client renders CONFIRM from this field; if the server computed it
    from rejections alone, 7B would show a live button on a dead import.
    """
    from datetime import UTC, datetime, timedelta

    from triviador.api.http.admin.imports import _summary
    from triviador.services.admin import ImportRecord, ImportStatus

    now = datetime(2026, 8, 20, 12, 0, tzinfo=UTC)
    record = ImportRecord(
        import_id="imp-1",
        uploaded_by="admin",
        upload_sha256="sha",
        filename="b.csv",
        staged_key="imp-1/b.csv",
        row_count=1,
        rejected_count=0,
        report={"rejections": [], "notices": []},
        status=ImportStatus.VALIDATED,
        expires_at=now - timedelta(seconds=1),
    )
    assert _summary(record, now=now).confirmable is False
    assert _summary(record, now=now - timedelta(hours=2)).confirmable is True


async def test_a_duplicate_prompt_is_a_notice_and_the_upload_stays_confirmable(
    admin_client: httpx.AsyncClient,
) -> None:
    """§10.2's rule, in the place it is easiest to get wrong: a repeated
    prompt inside one file, and a prompt the bank already holds, are both
    warnings. Rejecting either would make the upload unconfirmable, which
    is a block by another name."""
    first = await dry_run(admin_client, csv_bytes(NUM), "b.csv")
    await confirm(admin_client, first.json()["import_id"])

    again = (await dry_run(admin_client, csv_bytes(NUM, NUM), "b.csv")).json()
    assert again["rejected_count"] == 0
    assert again["confirmable"] is True
    reasons = " ".join(n["reason"] for n in again["notices"])
    assert "already in the bank" in reasons
    # `imports/parse.py`'s own wording (Task 7, unchanged here) is
    # "duplicate prompt: same as line N of this upload" — checked against
    # that established text rather than the brief's "same prompt as line",
    # which does not occur in the actual notice.
    assert "same as line" in reasons


async def test_a_missing_staged_object_is_refused_with_a_reason(
    admin_client: httpx.AsyncClient, deps: AppDependencies
) -> None:
    assert isinstance(deps.staging_store, FakeStagingStore)
    created = (await dry_run(admin_client, csv_bytes(NUM), "b.csv")).json()
    del deps.staging_store.objects[created["staged_key"]]
    response = await confirm(admin_client, created["import_id"])
    assert response.status_code == 409
    assert response.json()["code"] == "import_not_confirmable"


async def test_confirm_writes_the_media_blobs_before_the_rows(
    admin_client: httpx.AsyncClient, deps: AppDependencies
) -> None:
    assert isinstance(deps.questions_admin, FakeQuestionAdmin)
    assert isinstance(deps.media_store, FakeMediaStore)
    body = zip_bytes(csv_bytes(MC.replace(",,,", ",,,river.png")), {"river.png": png(40, 20)})
    created = (await dry_run(admin_client, body, "bank.zip")).json()
    await confirm(admin_client, created["import_id"])
    question = next(iter(deps.questions_admin.records.values()))
    assert question.media_asset_id is not None
    key = f"{question.media_asset_id[:2]}/{question.media_asset_id}.webp"
    assert deps.media_store.objects[key][:4] == b"RIFF"


async def test_an_unknown_category_in_the_file_is_created_by_confirm(
    admin_client: httpx.AsyncClient, deps: AppDependencies
) -> None:
    """The slug in the file is authoritative at confirm time: the dry-run
    already told the admin how many rows carry it, and refusing here would
    make every first import of a new topic a two-step dance."""
    assert isinstance(deps.categories, FakeCategories)
    created = (await dry_run(admin_client, csv_bytes(NUM), "b.csv")).json()
    await confirm(admin_client, created["import_id"])
    assert {c.slug for c in deps.categories.records.values()} == {"history"}


async def test_a_media_limit_tightened_since_the_dry_run_is_409_not_500(
    admin_client: httpx.AsyncClient, deps: AppDependencies, settings: Settings
) -> None:
    """§9.3 re-validates media at confirm time against whatever limits
    `ImageNormalizer` currently carries — built from settings at process
    start, not at dry-run time. An operator who tightens `media_max_bytes`
    between an admin's dry-run and their confirm (well inside
    `IMPORT_TTL_HOURS`) makes an image that passed then fail now. That is
    an ordinary "run the dry-run again", not a server fault; without the
    route catching `MediaRejected`, this would reach the catch-all handler
    as a 500 instead of the 409 every other unconfirmable-import case gets.
    """
    body = zip_bytes(csv_bytes(MC.replace(",,,", ",,,river.png")), {"river.png": png(40, 20)})
    created = (await dry_run(admin_client, body, "bank.zip")).json()

    # A fresh `ImageNormalizer` with `max_bytes=1`, mirroring a redeploy
    # that shipped a tighter limit — `dc_replace` keeps every other field
    # (crucially `staging_store` and `imports`) pointing at the exact same
    # objects the dry-run above already wrote to.
    tightened = dc_replace(deps, normalizer=ImageNormalizer(max_bytes=1, max_pixels=1, target_px=1))
    transport = httpx.ASGITransport(app=create_app(tightened), raise_app_exceptions=False)
    async with httpx.AsyncClient(
        transport=transport, base_url="http://testserver", headers={"Origin": ORIGIN}
    ) as tightened_client:
        tightened_client.cookies.set(settings.session_cookie_name, "tok-admin")
        response = await confirm(tightened_client, created["import_id"])

    assert response.status_code == 409
    assert response.json()["code"] == "import_not_confirmable"
