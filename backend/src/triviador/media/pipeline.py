"""§10.4: `upload → validate → re-encode → sha256 → key → row`.

**Order matters, and it is the order written here.** Format and dimensions
are read from the header *before* `load()` decodes anything: a 40000x40000
PNG is 200 KB on the wire and 6 GB in memory, and a bound checked after
decoding is a bound checked after the damage.

**The re-encode is the security control.** Not the mime check — a mime
type is a claim by the uploader — but the fact that the bytes we store are
bytes Pillow wrote from a decoded pixel buffer. Anything smuggled in the
original (appended script, EXIF payload, polyglot header) is not copied
because nothing is copied.
"""

import asyncio
import hashlib
import io
from dataclasses import dataclass

from PIL import Image, UnidentifiedImageError

# Raster formats a quiz question plausibly uses. SVG is absent by design
# (§10.4) and cannot be added here — Pillow does not decode it, so the
# refusal is structural rather than a list entry someone can extend.
ALLOWED_FORMATS = frozenset({"PNG", "JPEG", "WEBP", "GIF", "BMP"})

WEBP_QUALITY = 82


def object_key(asset_id: str) -> str:
    """§10.4's `/data/media/<ab>/<sha>.webp`, as an object key.

    The two-character fan-out is pointless in an object store, which has
    no directory to slow down — it is kept because the spec names this
    layout, a filesystem restore of the bucket benefits from it, and
    changing the key shape later rewrites every stored row.

    One function, not a pattern repeated at every call site: `asset_id` is
    always `NormalizedImage.sha256` by construction (`media_assets.id` is
    written from exactly that value in `admin/media.py`), so the write path
    (`NormalizedImage.storage_key`, below) and every read path that must
    resolve to the same Garage object afterward — the admin preview URL
    (`admin/media.py`'s `summary()`), the player-facing one
    (`api/schemas/games.py`'s `media_url()`), and the prefetch list
    (`api/projection/snapshot.py`'s `_media_prefetch`) — share this one
    definition instead of each reconstructing the shape independently,
    which is exactly how the player-facing one drifted from it: it built a
    bare `/media/<asset_id>`, with neither the fan-out directory nor the
    `.webp` extension, and Caddy proxies `/media/*` straight to Garage with
    no rewriting — so every question with an image 404s in a real browser,
    caught only once something actually served that URL through Caddy →
    Garage instead of asserting against the string in isolation.
    """
    return f"{asset_id[:2]}/{asset_id}.webp"


class MediaRejected(Exception):
    """The upload is not usable, and the reason is safe to show an admin."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


@dataclass(frozen=True)
class NormalizedImage:
    data: bytes
    sha256: str
    width: int
    height: int
    mime_type: str = "image/webp"

    @property
    def byte_size(self) -> int:
        return len(self.data)

    @property
    def storage_key(self) -> str:
        """See `object_key` above — this is that function, keyed on the
        upload's own freshly computed hash."""
        return object_key(self.sha256)


def normalize(raw: bytes, *, max_bytes: int, max_pixels: int, target_px: int) -> NormalizedImage:
    if len(raw) > max_bytes:
        raise MediaRejected(f"image is {len(raw)} bytes; the limit is {max_bytes}")

    try:
        with Image.open(io.BytesIO(raw)) as image:
            image_format = (image.format or "").upper()
            if image_format not in ALLOWED_FORMATS:
                raise MediaRejected(
                    f"{image_format or 'this file'} is not an accepted image format; "
                    f"use one of {', '.join(sorted(ALLOWED_FORMATS))}"
                )
            if max(image.size) > max_pixels:
                raise MediaRejected(
                    f"image is {image.width}x{image.height}; the limit is {max_pixels} px"
                )
            # `convert` forces the decode, so a truncated file fails here
            # rather than halfway through `save`. RGB drops alpha and any
            # palette — WebP would keep both, and neither survives a
            # question thumbnail usefully.
            frame = image.convert("RGB")
    except UnidentifiedImageError as exc:
        raise MediaRejected("that file is not an image this server can decode") from exc
    except Image.DecompressionBombError as exc:
        raise MediaRejected("image is implausibly large when decoded") from exc
    except OSError as exc:
        raise MediaRejected("image is corrupt or truncated") from exc

    # `thumbnail` only ever shrinks, preserves the aspect ratio, and is a
    # no-op below the target — which is exactly §10.4's "max 1280 px".
    frame.thumbnail((target_px, target_px))
    buffer = io.BytesIO()
    # No `exif=`, no `icc_profile=`: metadata is dropped by omission,
    # which is stronger than stripping it afterwards.
    frame.save(buffer, format="WEBP", quality=WEBP_QUALITY, method=4)
    data = buffer.getvalue()
    return NormalizedImage(
        data=data,
        sha256=hashlib.sha256(data).hexdigest(),
        width=frame.width,
        height=frame.height,
    )


class ImageNormalizer:
    """§9.2: one encode at a time, off the event loop.

    A 200-image bulk import shares a process with live games (ADR-002).
    Unbounded decoding there stalls command processing for every match in
    flight — and `to_thread` alone would only move the stall into 200
    threads competing for the same cores. The semaphore is what bounds it.

    Built in the composition root and passed around, never a module-level
    global: an `asyncio.Semaphore` at import time is shared by every test
    in a session, which is how a suite becomes order-dependent.
    """

    def __init__(self, *, max_bytes: int, max_pixels: int, target_px: int) -> None:
        self._semaphore = asyncio.Semaphore(1)
        self._limits = {
            "max_bytes": max_bytes,
            "max_pixels": max_pixels,
            "target_px": target_px,
        }

    async def normalize(self, raw: bytes) -> NormalizedImage:
        async with self._semaphore:
            return await asyncio.to_thread(normalize, raw, **self._limits)
