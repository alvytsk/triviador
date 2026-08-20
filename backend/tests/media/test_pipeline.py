"""Pure: no database, no object store, no event loop.

Every assertion here is a §10.4 sentence. The one that is not obviously a
security control — "re-encode to WebP" — is the strongest one: it is what
makes an uploaded file stop being the attacker's file.
"""

import asyncio
import io

import pytest
from PIL import Image

from triviador.media.pipeline import MediaRejected, normalize

pytestmark = pytest.mark.asyncio

LIMITS = {"max_bytes": 5_242_880, "max_pixels": 4000, "target_px": 1280}

SVG = b'<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>'


def png(width: int, height: int, colour: tuple[int, int, int] = (10, 20, 30)) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (width, height), colour).save(buffer, format="PNG")
    return buffer.getvalue()


def jpeg_with_exif(width: int = 64, height: int = 64) -> bytes:
    buffer = io.BytesIO()
    image = Image.new("RGB", (width, height), (200, 100, 50))
    exif = image.getexif()
    exif[0x010F] = "SecretCameraMaker"   # Make
    exif[0x9286] = "GPS-tagged holiday"  # UserComment
    image.save(buffer, format="JPEG", exif=exif)
    return buffer.getvalue()


def test_a_png_becomes_a_webp() -> None:
    result = normalize(png(64, 48), **LIMITS)
    assert result.mime_type == "image/webp"
    assert result.data[:4] == b"RIFF" and result.data[8:12] == b"WEBP"
    assert (result.width, result.height) == (64, 48)


def test_the_key_is_the_content_hash() -> None:
    """Content addressing is what makes re-upload, re-import and
    `media-gc` idempotent, so the key is derived from the *output* bytes,
    never from the filename or the input."""
    result = normalize(png(64, 48), **LIMITS)
    assert result.storage_key == f"{result.sha256[:2]}/{result.sha256}.webp"
    assert normalize(png(64, 48), **LIMITS).sha256 == result.sha256


def test_a_larger_image_is_downscaled_to_the_target() -> None:
    result = normalize(png(3200, 1600), **LIMITS)
    assert max(result.width, result.height) == 1280
    assert (result.width, result.height) == (1280, 640)


def test_a_smaller_image_is_not_upscaled() -> None:
    result = normalize(png(200, 100), **LIMITS)
    assert (result.width, result.height) == (200, 100)


def test_exif_does_not_survive() -> None:
    """§10.4 strips metadata. A holiday photo carries GPS coordinates, and
    an admin uploading one to a quiz question has not consented to
    publishing their home address on an anonymously readable bucket."""
    result = normalize(jpeg_with_exif(), **LIMITS)
    with Image.open(io.BytesIO(result.data)) as reencoded:
        assert not dict(reencoded.getexif())
    assert b"SecretCameraMaker" not in result.data


def test_svg_is_refused() -> None:
    """Not sanitised — refused. SVG executes script, and the pipeline's
    whole defence is that the bytes served are bytes we produced."""
    with pytest.raises(MediaRejected, match="image"):
        normalize(SVG, **LIMITS)


def test_a_payload_hidden_in_a_raster_does_not_survive_the_re_encode() -> None:
    raw = png(64, 64) + b"<script>alert('appended')</script>"
    result = normalize(raw, **LIMITS)
    assert b"<script>" not in result.data


def test_an_oversized_upload_is_refused_before_decoding() -> None:
    with pytest.raises(MediaRejected, match="5242880"):
        normalize(b"x" * 5_242_881, **LIMITS)


def test_an_image_beyond_the_pixel_bound_is_refused() -> None:
    """Checked from the header, before `load()`: decoding first is how a
    decompression bomb gets to allocate its gigabyte."""
    with pytest.raises(MediaRejected, match="4000"):
        normalize(png(4001, 10), **LIMITS)


def test_a_truncated_file_is_a_rejection_not_a_crash() -> None:
    with pytest.raises(MediaRejected):
        normalize(png(64, 64)[:60], **LIMITS)


async def test_only_one_encode_runs_at_a_time(monkeypatch: pytest.MonkeyPatch) -> None:
    """The semaphore, asserted rather than assumed: without it, ten
    concurrent uploads decode ten images on ten threads while a game is
    waiting for its next command."""
    import triviador.media.pipeline as pipeline_module

    live = 0
    peak = 0
    real = pipeline_module.normalize

    def instrumented(raw: bytes, **kwargs: int) -> pipeline_module.NormalizedImage:
        nonlocal live, peak
        live += 1
        peak = max(peak, live)
        try:
            return real(raw, **kwargs)
        finally:
            live -= 1

    monkeypatch.setattr(pipeline_module, "normalize", instrumented)
    normalizer = pipeline_module.ImageNormalizer(**LIMITS)
    await asyncio.gather(*(normalizer.normalize(png(64, 64, (i, i, i))) for i in range(10)))
    assert peak == 1
