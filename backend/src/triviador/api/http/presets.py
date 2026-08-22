"""`GET /api/presets` — the one preset route that is not admin-only.

Spec 1B §6.1 lists presets under `/api/admin` alone. This route is a
deliberate addition (Plan 7A, Decision 1): `POST /api/games` takes a
`preset_id`, and without a way to list them the parameter is unusable and
every game runs whatever "default" currently means. Read-only, active
presets only, any signed-in user — the same standing `GET /api/maps` has.
"""

from dataclasses import asdict

from fastapi import APIRouter

from triviador.api.deps import Deps, Principal
from triviador.api.schemas.presets import PresetSummary, RulesView

router = APIRouter(prefix="/api/presets", tags=["presets"])


@router.get("")
async def list_presets(deps: Deps, principal: Principal) -> list[PresetSummary]:
    default = await deps.presets.get_default()
    return [
        PresetSummary(
            id=record.preset_id,
            name=record.name,
            is_default=default is not None and default.preset_id == record.preset_id,
            rules=RulesView(**asdict(record.rules)),
        )
        for record in await deps.presets.list_active()
    ]
