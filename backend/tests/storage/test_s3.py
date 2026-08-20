import pytest

from triviador.storage.s3 import S3ImportStagingStore, S3MediaStore

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


async def test_put_then_open_returns_the_bytes(media_store: S3MediaStore, prefix: str) -> None:
    await media_store.put(
        f"{prefix}/a.webp", b"payload", content_type="image/webp", cache_control="immutable"
    )
    assert await media_store.open(f"{prefix}/a.webp") == b"payload"


async def test_open_of_a_missing_key_is_none_not_an_exception(
    media_store: S3MediaStore, prefix: str
) -> None:
    """`None`, because "no such asset" is an ordinary answer on the
    `media-gc` and media-serving paths, and a caller that has to catch
    `ClientError` to learn it is a caller that eventually catches a
    credentials failure by mistake."""
    assert await media_store.open(f"{prefix}/absent.webp") is None


async def test_delete_is_idempotent(media_store: S3MediaStore, prefix: str) -> None:
    """`media-gc` deletes the object and then updates the row; a crash
    between the two makes the next run repeat the delete. If that raised,
    the sweep could never finish."""
    await media_store.put(f"{prefix}/b.webp", b"x", content_type="image/webp")
    await media_store.delete(f"{prefix}/b.webp")
    await media_store.delete(f"{prefix}/b.webp")
    assert await media_store.open(f"{prefix}/b.webp") is None


async def test_cache_control_is_stored_on_the_object(
    media_store: S3MediaStore, prefix: str
) -> None:
    """§9.2: the header is object metadata set at PUT time, so Garage
    returns it on a 200 and — correctly — not on a 404. A proxy-level
    header would attach a one-year lifetime to error responses."""
    await media_store.put(
        f"{prefix}/c.webp",
        b"x",
        content_type="image/webp",
        cache_control="public, max-age=31536000, immutable",
    )
    head = await media_store.head(f"{prefix}/c.webp")
    assert head is not None
    assert head.cache_control == "public, max-age=31536000, immutable"
    assert head.content_type == "image/webp"


async def test_list_objects_paginates_past_one_thousand(
    media_store: S3MediaStore, prefix: str
) -> None:
    """S3 truncates a listing at 1000 keys. `media-gc` compares the store
    against the database, so a listing that silently stops at 1000 would
    make every asset past the first thousand invisible — and therefore
    never collected."""
    for i in range(1002):
        await media_store.put(f"{prefix}/{i}.webp", b"x", content_type="image/webp")
    listed = await media_store.list_objects(prefix=prefix)
    assert len(listed) == 1002


async def test_a_listing_carries_the_age_the_grace_period_needs(
    media_store: S3MediaStore, prefix: str
) -> None:
    """`media-gc` skips objects younger than `media_gc_grace_minutes`,
    which it can only do if the listing says how old they are."""
    await media_store.put(f"{prefix}/fresh.webp", b"x", content_type="image/webp")
    listed = await media_store.list_objects(prefix=f"{prefix}/fresh")
    assert listed[0].last_modified.tzinfo is not None


async def test_the_staging_store_writes_to_a_different_bucket(
    staging_store: S3ImportStagingStore, media_store: S3MediaStore, prefix: str
) -> None:
    """The security boundary of §9.1, asserted rather than assumed: a key
    written to staging is not readable from the public media bucket."""
    await staging_store.put(f"{prefix}/raw.zip", b"secret", content_type="application/zip")
    assert await staging_store.open(f"{prefix}/raw.zip") == b"secret"
    assert await media_store.open(f"{prefix}/raw.zip") is None
