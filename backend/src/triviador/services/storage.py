"""Two object stores, because §9.1 makes them two buckets.

`MediaStore` is website-enabled and anonymously readable; every object in
it is a normalized WebP whose key is its own content hash.
`ImportStagingStore` is private, holds the raw bytes an admin uploaded —
answer keys included — and expires by lifecycle.

They are declared as two Protocols rather than one store plus a prefix
convention for the reason §9.1 states: the security boundary is the
bucket, and a prefix bug in the wrong direction publishes unvalidated
uploads. Structurally `MediaStore` is a superset (`head`, `list_objects`), so the
type system alone will not stop a caller from passing the wrong one — the
composition root is where they are told apart, and
`tests/api/test_admin_wiring.py` asserts the two adapters carry different
bucket names.
"""

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol


@dataclass(frozen=True)
class ObjectHead:
    """What a `HEAD` answers, and nothing more. `media-gc` needs ages;
    the upload path needs to know the object is still there; nobody needs
    the body."""

    byte_size: int
    content_type: str
    cache_control: str | None
    last_modified: datetime


@dataclass(frozen=True)
class StoredObject:
    """One entry of a listing.

    `last_modified` is part of it because `media-gc`'s orphan pass is
    age-aware: an object with no database row is either garbage from a
    failed transaction (§10.3) or an upload whose row has not committed
    yet, and only its age tells the two apart.
    """

    key: str
    byte_size: int
    last_modified: datetime


class ImportStagingStore(Protocol):
    async def put(self, key: str, data: bytes, *, content_type: str) -> None: ...

    async def open(self, key: str) -> bytes | None:
        """`None` for a missing key, never an exception: "the staged object
        is gone" is an ordinary state of §9.3's expiry machine, reached by
        every confirmed import and every restore."""
        ...

    async def delete(self, key: str) -> None:
        """Idempotent. §9.3 deletes the object and then updates the row, so
        a crash between the two means the next sweep repeats the delete."""
        ...


class MediaStore(Protocol):
    async def put(
        self, key: str, data: bytes, *, content_type: str, cache_control: str | None = None
    ) -> None: ...
    async def open(self, key: str) -> bytes | None: ...
    async def head(self, key: str) -> ObjectHead | None: ...
    async def delete(self, key: str) -> None: ...

    async def list_objects(self, *, prefix: str = "") -> tuple[StoredObject, ...]:
        """Every object, paginated to exhaustion. `media-gc` compares this
        listing against the database; a truncated one under-reports and
        leaves orphans uncollected forever."""
        ...
