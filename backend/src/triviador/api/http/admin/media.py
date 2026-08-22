"""`POST /api/admin/media`: one image, one blob, one row.

**A raw body, not multipart.** §10.1's surface says only "media upload".
Multipart would buy a filename we do not store (the key is the content
hash) and a form field we do not have, in exchange for parsing a format
whose bounds are hard to enforce while streaming. The client sends the
file as the body with its own `Content-Type`; the type is a hint the
pipeline ignores, since the format is read from the bytes.

**Order: blob first, row second.** A failed row insert leaves an
unreferenced blob that `media-gc` collects. The reverse leaves a row
pointing at nothing, which is a broken question nobody can repair.
"""

from fastapi import APIRouter, Request, Response

from triviador.api.deps import AdminPrincipal, Deps
from triviador.api.errors import ApiError, ApiErrorCode
from triviador.api.schemas.admin.media import MediaAssetSummary
from triviador.media.pipeline import MediaRejected, NormalizedImage, object_key

router = APIRouter(tags=["admin"])

CACHE_CONTROL = "public, max-age=31536000, immutable"


async def read_capped(request: Request, max_bytes: int) -> bytes:
    """Read the stream, refusing as soon as the cap is passed.

    Shared with the import route (Task 7). Reading to the end and then
    checking the length would hold 32 MiB of somebody else's problem in
    memory before answering 413 — which is the same reasoning
    `BodyLimitMiddleware` gives, applied at the route that opted out of it.
    """
    chunks: list[bytes] = []
    total = 0
    async for chunk in request.stream():
        total += len(chunk)
        if total > max_bytes:
            raise ApiError(ApiErrorCode.PAYLOAD_TOO_LARGE, 413, f"upload exceeds {max_bytes} bytes")
        chunks.append(chunk)
    return b"".join(chunks)


async def repair_blob(deps: Deps, image: NormalizedImage) -> None:
    """Make sure the object is still there now that the row is committed.

    The window this closes: `media-gc`'s orphan pass deletes objects with
    no database row, and between this route's `put` and its `ensure` there
    *is* no row. A sweep running in that instant takes the blob and leaves
    a row pointing at nothing — a broken image nobody can repair from the
    editor, because re-uploading the same file produces the same content
    hash and finds the row already present.

    `media-gc` also skips objects younger than `media_gc_grace_minutes`,
    so this repair should never fire. It costs one `HEAD` on a LAN and is
    the only fix that needs no lock and no transaction spanning a network
    write — see Decision 9.
    """
    if await deps.media_store.head(image.storage_key) is None:
        await deps.media_store.put(
            image.storage_key,
            image.data,
            content_type=image.mime_type,
            cache_control=CACHE_CONTROL,
        )


def summary(
    image_id: str, *, media_base: str, width: int | None, height: int | None, byte_size: int
) -> MediaAssetSummary:
    return MediaAssetSummary(
        id=image_id,
        url=f"{media_base}/{object_key(image_id)}",
        width=width,
        height=height,
        byte_size=byte_size,
    )


@router.post("/media", status_code=201)
async def upload_media(
    request: Request, response: Response, deps: Deps, principal: AdminPrincipal
) -> MediaAssetSummary:
    raw = await read_capped(request, deps.settings.media_max_bytes)
    try:
        image: NormalizedImage = await deps.normalizer.normalize(raw)
    except MediaRejected as exc:
        # 415, not 422: the request was well-formed, its *media type* is
        # the thing this server will not accept.
        raise ApiError(ApiErrorCode.MEDIA_REJECTED, 415, exc.reason) from exc

    await deps.media_store.put(
        image.storage_key,
        image.data,
        content_type=image.mime_type,
        cache_control=CACHE_CONTROL,
    )
    record, created = await deps.media_assets.ensure(
        asset_id=image.sha256,
        mime_type=image.mime_type,
        width=image.width,
        height=image.height,
        byte_size=image.byte_size,
        storage_key=image.storage_key,
        created_by=str(principal.user_id),
    )
    await repair_blob(deps, image)
    if not created:
        response.status_code = 200
    return summary(
        record.asset_id,
        media_base=deps.settings.media_public_base,
        width=record.width,
        height=record.height,
        byte_size=record.byte_size,
    )
