import httpx

from tests.api.conftest import ORIGIN
from tests.api.fakes import FakeMediaStore
from tests.media.test_pipeline import SVG, png
from triviador.api.deps import AppDependencies

# No module-level `pytestmark = pytest.mark.asyncio`: `asyncio_mode = "auto"`
# (pyproject.toml) already collects every `async def test_*` here without
# it, and this file also has a sync test
# (`test_every_exempt_upload_path_is_a_real_route`) that the mark would
# otherwise land on too, which pytest-asyncio warns about on every run.


async def _upload(client: httpx.AsyncClient, body: bytes, content_type: str) -> httpx.Response:
    return await client.post(
        "/api/admin/media", content=body, headers={"Content-Type": content_type, "Origin": ORIGIN}
    )


async def test_a_player_cannot_upload(signed_in: httpx.AsyncClient) -> None:
    assert (await _upload(signed_in, png(8, 8), "image/png")).status_code == 403


async def test_an_upload_is_stored_re_encoded_and_addressed_by_content(
    admin_client: httpx.AsyncClient, deps: AppDependencies
) -> None:
    response = await _upload(admin_client, png(64, 32), "image/png")
    assert response.status_code == 201
    body = response.json()
    assert body["width"] == 64 and body["height"] == 32
    assert body["url"] == f"/media/{body['id'][:2]}/{body['id']}.webp"
    assert isinstance(deps.media_store, FakeMediaStore)
    stored = deps.media_store.objects[f"{body['id'][:2]}/{body['id']}.webp"]
    assert stored[:4] == b"RIFF"


async def test_the_object_carries_the_immutable_cache_header(
    admin_client: httpx.AsyncClient, deps: AppDependencies
) -> None:
    body = (await _upload(admin_client, png(8, 8), "image/png")).json()
    key = f"{body['id'][:2]}/{body['id']}.webp"
    assert isinstance(deps.media_store, FakeMediaStore)
    assert deps.media_store.metadata[key] == (
        "image/webp",
        "public, max-age=31536000, immutable",
    )


async def test_re_uploading_the_same_image_answers_200_with_the_same_id(
    admin_client: httpx.AsyncClient,
) -> None:
    first = await _upload(admin_client, png(16, 16), "image/png")
    second = await _upload(admin_client, png(16, 16), "image/png")
    assert (first.status_code, second.status_code) == (201, 200)
    assert first.json()["id"] == second.json()["id"]


async def test_an_svg_is_refused_with_a_reason(admin_client: httpx.AsyncClient) -> None:
    response = await _upload(admin_client, SVG, "image/svg+xml")
    assert response.status_code == 415
    assert response.json()["code"] == "media_rejected"


async def test_a_blob_deleted_between_put_and_row_is_restored(
    admin_client: httpx.AsyncClient, deps: AppDependencies
) -> None:
    """Decision 9's repair, driven directly: with the row committed and
    the object gone, the route must put it back rather than answer 201 for
    an asset that is not there."""
    body = png(24, 24)
    first = (await _upload(admin_client, body, "image/png")).json()
    key = f"{first['id'][:2]}/{first['id']}.webp"
    assert isinstance(deps.media_store, FakeMediaStore)
    del deps.media_store.objects[key]
    second = await _upload(admin_client, body, "image/png")
    assert second.status_code == 200
    assert key in deps.media_store.objects


async def test_a_body_over_the_media_cap_is_refused_by_the_route(
    admin_client: httpx.AsyncClient, deps: AppDependencies
) -> None:
    """The global body limit does not apply here (Task 1); the route's own
    cap does, and it stops reading rather than buffering the whole body."""
    oversized = b"x" * (deps.settings.media_max_bytes + 1)
    response = await _upload(admin_client, oversized, "image/png")
    assert response.status_code == 413
    assert response.json()["code"] == "payload_too_large"


def test_every_exempt_upload_path_is_a_real_route(deps: AppDependencies) -> None:
    """`UPLOAD_PATHS` is a hole in the body limit. A stale entry is a hole
    pointing at nothing, and a renamed route is a route that silently
    starts buffering at 1 MiB again.

    `api_routes` rather than `app.routes`: the latter holds
    `_IncludedRouter` wrappers, so an `isinstance(r, APIRoute)` filter over
    it returns nothing and this assertion would pass on any input (Task 1
    established this; its `test_the_walk_reaches_real_routes` is the
    tripwire).
    """
    from tests.api.conftest import api_routes
    from triviador.api.app import create_app
    from triviador.api.http.admin import UPLOAD_PATHS

    paths = {mounted.path for mounted in api_routes(create_app(deps))}
    assert set(UPLOAD_PATHS) - paths == set()
