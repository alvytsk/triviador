import httpx
import pytest

from tests.api.fakes import FakeCategories, FakeMediaStore, FakeQuestionAdmin, FakeStagingStore
from tests.imports.test_parse import MC, NUM, csv_bytes, zip_bytes
from tests.media.test_pipeline import png
from triviador.api.deps import AppDependencies

pytestmark = pytest.mark.asyncio


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
    await dry_run(admin_client, zip_bytes(csv_bytes(MC.replace(",,,", ",,,river.png")),
                                          {"river.png": png(32, 32)}), "bank.zip")
    assert (len(deps.questions_admin.records), len(deps.media_store.objects)) == before
    assert deps.categories.records == {}


async def test_a_rejected_row_makes_the_upload_unconfirmable(
    admin_client: httpx.AsyncClient
) -> None:
    """§10.3: CONFIRM is enabled only when `rejected == 0`. The server says
    so on the report rather than leaving the rule to the client."""
    response = await dry_run(admin_client, csv_bytes(MC, "numeric,No answer,history,easy,,,,,,,,"),
                             "b.csv")
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
