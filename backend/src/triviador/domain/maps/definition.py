from collections.abc import Mapping
from dataclasses import dataclass

from triviador.domain.ids import MapId, RegionId


@dataclass(frozen=True)
class Region:
    region_id: RegionId
    display_name: str


@dataclass(frozen=True)
class MapDefinition:
    """Immutable board topology. Loaded from data/maps/<id>/map.json."""

    map_id: MapId
    regions: tuple[Region, ...]
    adjacency: Mapping[RegionId, frozenset[RegionId]]

    def region_ids(self) -> tuple[RegionId, ...]:
        return tuple(r.region_id for r in self.regions)

    def neighbours(self, region_id: RegionId) -> frozenset[RegionId]:
        return self.adjacency.get(region_id, frozenset())
