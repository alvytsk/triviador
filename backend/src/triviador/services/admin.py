"""What the admin surface asks of the database, as Protocols.

Same rule as `ports.py` and `identity.py`: `api/` depends on these, `db/`
implements them, neither imports the other, and `tests/api/` runs the
whole admin surface against in-memory fakes with no PostgreSQL.

One port per resource rather than one `AdminPort` with thirty methods:
`tests/api/fakes.py` has to implement whatever a route touches, and a
single wide port would make every fake grow a method for every route in
the plan.
"""

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class MediaAssetRecord:
    asset_id: str
    mime_type: str
    width: int | None
    height: int | None
    byte_size: int
    storage_key: str


class MediaAssetPort(Protocol):
    async def ensure(
        self,
        *,
        asset_id: str,
        mime_type: str,
        width: int,
        height: int,
        byte_size: int,
        storage_key: str,
        created_by: str,
    ) -> tuple[MediaAssetRecord, bool]:
        """The record, and whether this call created it.

        Two admins uploading the same image produce the same sha256 and so
        the same row; the boolean is what lets the route answer 201 the
        first time and 200 afterwards rather than raising on a primary-key
        collision that means "this already worked".
        """
        ...

    async def get(self, asset_id: str) -> MediaAssetRecord | None: ...
