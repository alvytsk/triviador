from fastapi import APIRouter

from triviador.api.deps import Deps, Principal
from triviador.api.errors import ApiError, ApiErrorCode
from triviador.api.schemas.maps import MapDetail, MapRegion, MapSummary
from triviador.domain.ids import MapId
from triviador.maps.registry import InvalidMapError

router = APIRouter(prefix="/api/maps", tags=["maps"])


@router.get("")
async def list_maps(deps: Deps, principal: Principal) -> list[MapSummary]:
    summaries = []
    for map_id in deps.maps.available():
        loaded = deps.maps.load_with_digest(map_id)
        summaries.append(
            MapSummary(map_id=str(map_id), region_count=len(loaded.definition.regions))
        )
    return summaries


@router.get("/{map_id}")
async def get_map(map_id: str, deps: Deps, principal: Principal) -> MapDetail:
    try:
        loaded = deps.maps.load_with_digest(MapId(map_id))
    except InvalidMapError as exc:
        # An unregistered id and a corrupt `map.json` are both 404 to a
        # client: neither is a map it can play on, and the difference is an
        # operator's problem, visible in the log.
        raise ApiError(ApiErrorCode.MAP_UNKNOWN, 404, "no such map") from exc
    return MapDetail(
        map_id=str(loaded.definition.map_id),
        svg_url=f"{deps.settings.maps_public_base}/{map_id}/map.svg",
        regions=tuple(
            MapRegion(region_id=str(r.region_id), display_name=r.display_name)
            for r in loaded.definition.regions
        ),
    )
