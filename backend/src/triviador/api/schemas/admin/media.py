from pydantic import BaseModel, ConfigDict


class MediaAssetSummary(BaseModel):
    """What the editor needs to show a thumbnail and store a reference.

    `url` is built by the server from `media_public_base`, exactly as the
    projection does for in-game question media (§8.7) — the client never
    concatenates a base with a key, so the two can never disagree.
    """

    model_config = ConfigDict(extra="forbid")

    id: str
    url: str
    width: int | None
    height: int | None
    byte_size: int
