"""`PresetSummary` — the one preset shape a player ever sees.

Public, unlike everything else under `api/schemas/admin/`: `GET
/api/presets` (Plan 7A Decision 1) hands this to any signed-in user so
`POST /api/games`'s `preset_id` is a choice a lobby can actually offer.
"""

from pydantic import BaseModel, ConfigDict


class RulesView(BaseModel):
    """`GameRules`, field for field. Written out rather than generated
    from the dataclass so the contract is reviewable in the diff — this
    model is what the lobby's rules readout renders."""

    model_config = ConfigDict(extra="forbid")

    player_count: int
    expansion_rounds: int
    battle_rounds: int
    base_hp: int
    answer_timeout_ms: int
    pick_timeout_ms: int
    warmup_ms: int
    claims_by_rank: list[int]
    pts_base: int
    pts_territory: int
    pts_conquered: int
    pts_defense: int


class PresetSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    name: str
    is_default: bool
    rules: RulesView
