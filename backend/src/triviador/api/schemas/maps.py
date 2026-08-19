from pydantic import BaseModel, ConfigDict


class MapSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    map_id: str
    region_count: int


class MapRegion(BaseModel):
    model_config = ConfigDict(extra="forbid")

    region_id: str
    display_name: str


class MapDetail(BaseModel):
    """No adjacency field, for the same structural reason `ClientQuestion`
    has no `is_correct`: a field that does not exist cannot be serialized
    by accident."""

    model_config = ConfigDict(extra="forbid")

    map_id: str
    svg_url: str
    regions: tuple[MapRegion, ...]
