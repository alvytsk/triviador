"""Read-only preset lookup. CRUD is Plan 7."""

from dataclasses import fields

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from triviador.db.models.presets import RulePreset
from triviador.domain.game.rules import GameRules, validate_rules
from triviador.services.ports import PresetRecord


def _to_rules(raw: dict[str, object]) -> GameRules:
    """JSONB back into the frozen dataclass, then validated.

    `claims_by_rank` round-trips through JSON as a list; `GameRules` is
    compared by value in tests and hashed nowhere, but a list where a tuple
    belongs is a shape difference that shows up much later as an inequality
    nobody expects.
    """
    names = {f.name for f in fields(GameRules)}
    missing = names - set(raw)
    if missing:
        raise ValueError(f"preset rules are missing {sorted(missing)}")
    kwargs = {k: v for k, v in raw.items() if k in names}
    kwargs["claims_by_rank"] = tuple(kwargs["claims_by_rank"])  # type: ignore[arg-type]
    rules = GameRules(**kwargs)  # type: ignore[arg-type]
    problems = validate_rules(rules)
    if problems:
        raise ValueError("preset rules are invalid: " + "; ".join(problems))
    return rules


class PresetRepository:
    def __init__(self, sessionmaker: async_sessionmaker[AsyncSession]) -> None:
        self._sessionmaker = sessionmaker

    async def get(self, preset_id: str) -> PresetRecord | None:
        return await self._one(RulePreset.id == preset_id)

    async def get_default(self) -> PresetRecord | None:
        return await self._one(RulePreset.is_default)

    async def _one(self, criterion: object) -> PresetRecord | None:
        async with self._sessionmaker() as session:
            result = await session.execute(
                select(RulePreset).where(criterion, RulePreset.is_active)  # type: ignore[arg-type]
            )
            preset = result.scalar_one_or_none()
        if preset is None:
            return None
        return PresetRecord(preset.id, preset.name, _to_rules(preset.rules))
