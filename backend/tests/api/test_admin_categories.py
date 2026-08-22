import httpx
import pytest

pytestmark = pytest.mark.asyncio


async def test_a_player_cannot_list_categories(signed_in: httpx.AsyncClient) -> None:
    assert (await signed_in.get("/api/admin/categories")).status_code == 403


async def test_create_then_list(admin_client: httpx.AsyncClient) -> None:
    created = await admin_client.post(
        "/api/admin/categories", json={"slug": "geography", "name": "Geography"}
    )
    assert created.status_code == 201
    listed = await admin_client.get("/api/admin/categories")
    assert {c["slug"] for c in listed.json()} >= {"geography"}


async def test_a_duplicate_slug_is_409_not_500(admin_client: httpx.AsyncClient) -> None:
    """`categories.slug` is UNIQUE. Without a deliberate check the second
    create surfaces as `IntegrityError` → 503 `database_unavailable`,
    which tells the admin the database is down when their input was
    simply already there."""
    body = {"slug": "film", "name": "Film"}
    assert (await admin_client.post("/api/admin/categories", json=body)).status_code == 201
    duplicate = await admin_client.post("/api/admin/categories", json=body)
    assert duplicate.status_code == 409
    assert duplicate.json()["code"] == "slug_taken"


async def test_a_slug_is_lowercase_and_dashed(admin_client: httpx.AsyncClient) -> None:
    response = await admin_client.post(
        "/api/admin/categories", json={"slug": "Pop Music", "name": "Pop"}
    )
    assert response.status_code == 422


async def test_rename_keeps_the_slug(admin_client: httpx.AsyncClient) -> None:
    """The slug is an identifier the seed CSV and the import format both
    reference by value (`category_slug`); renaming the display name must
    not silently repoint every future import."""
    created = (
        await admin_client.post("/api/admin/categories", json={"slug": "sport", "name": "Sport"})
    ).json()
    renamed = await admin_client.patch(
        f"/api/admin/categories/{created['id']}", json={"name": "Sports"}
    )
    assert renamed.status_code == 200
    assert renamed.json() == {"id": created["id"], "slug": "sport", "name": "Sports"}
