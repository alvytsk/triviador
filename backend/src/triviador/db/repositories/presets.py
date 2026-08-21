"""Read-only preset lookup, and the CRUD behind §10.6's admin screen
(Plan 7A Task 12)."""

from dataclasses import asdict, fields
from uuid import uuid4

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from triviador.db.models.presets import RulePreset
from triviador.domain.game.rules import GameRules, validate_rules
from triviador.services.admin import DeactivateOutcome, PresetAdminRecord, UpdateOutcome
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

    async def get(self, preset_id: str) -> PresetAdminRecord | None:
        return await self._one(RulePreset.id == preset_id)

    async def get_default(self) -> PresetAdminRecord | None:
        return await self._one(RulePreset.is_default)

    async def _one(self, criterion: object) -> PresetAdminRecord | None:
        async with self._sessionmaker() as session:
            result = await session.execute(
                select(RulePreset).where(criterion, RulePreset.is_active)  # type: ignore[arg-type]
            )
            preset = result.scalar_one_or_none()
        if preset is None:
            return None
        return PresetAdminRecord(
            preset.id, preset.name, _to_rules(preset.rules), preset.is_default, preset.is_active
        )

    async def get_including_retired(self, preset_id: str) -> PresetAdminRecord | None:
        """The admin's single-item read (`GET /api/admin/presets/{id}` and
        its `/coverage`), which must see what `list_all` sees.

        `get`/`_one` filter on `is_active` for `PresetPort`'s sake — a
        player must never resolve `preset_id` to a retired ruleset — but
        the admin list deliberately shows retired presets, and a detail
        view that 404s on exactly those rows makes the `is_active` field it
        renders unreachable. Unfiltered by design, not an oversight.
        """
        async with self._sessionmaker() as session:
            preset = await session.get(RulePreset, preset_id)
        if preset is None:
            return None
        return PresetAdminRecord(
            preset.id, preset.name, _to_rules(preset.rules), preset.is_default, preset.is_active
        )

    async def list_active(self) -> tuple[PresetRecord, ...]:
        """The public read (`GET /api/presets`). Active only — a retired
        preset must not be selectable, and `is_active` is exactly what
        retirement means here."""
        async with self._sessionmaker() as session:
            rows = (
                await session.execute(
                    select(RulePreset).where(RulePreset.is_active).order_by(RulePreset.name)
                )
            ).scalars().all()
        return tuple(PresetRecord(r.id, r.name, _to_rules(r.rules)) for r in rows)

    async def list_all(self) -> tuple[PresetAdminRecord, ...]:
        """The admin read: retired presets included, `is_default` and
        `is_active` exposed, because retiring and promoting are exactly
        what this screen does."""
        async with self._sessionmaker() as session:
            rows = (
                await session.execute(select(RulePreset).order_by(RulePreset.name))
            ).scalars().all()
        return tuple(
            PresetAdminRecord(r.id, r.name, _to_rules(r.rules), r.is_default, r.is_active)
            for r in rows
        )

    async def create(
        self, *, name: str, rules: GameRules, is_default: bool
    ) -> PresetAdminRecord:
        async with self._sessionmaker() as session, session.begin():
            if is_default:
                await self._clear_default(session)
            preset = RulePreset(
                id=str(uuid4()),
                name=name,
                rules=asdict(rules),
                is_default=is_default,
                version=1,
                is_active=True,
            )
            session.add(preset)
        return PresetAdminRecord(preset.id, name, rules, is_default, True)

    async def update(
        self, preset_id: str, *, name: str, rules: GameRules, is_default: bool
    ) -> tuple[UpdateOutcome, PresetAdminRecord | None]:
        """Editing a preset does not touch a running game: `games.rules`
        holds a frozen copy taken at creation (§6.2), which is why
        `version` is bumped here for the admin screen's benefit and
        nothing else has to be notified.

        **Two default transitions are refused, both inside this
        transaction.** The database enforces *at most one* default with a
        partial unique index; "never zero, and never a retired one" is
        application logic, and `deactivate` is not the only door into it:

            default → is_default=false     leaves the system with no
                                           default at all, and
                                           `POST /api/games` with
                                           `preset_id: null` then 409s with
                                           `no_default_preset` for everyone

            retired → is_default=true      makes a default `get_default()`
                                           cannot return, because it filters
                                           on `is_active` — the same outage,
                                           reached from the other side
        """
        async with self._sessionmaker() as session, session.begin():
            row = await session.get(RulePreset, preset_id, with_for_update=True)
            if row is None:
                return UpdateOutcome.NOT_FOUND, None
            if row.is_default and not is_default:
                return UpdateOutcome.WOULD_LEAVE_NO_DEFAULT, None
            if is_default and not row.is_active:
                return UpdateOutcome.RETIRED_CANNOT_BE_DEFAULT, None
            if is_default and not row.is_default:
                await self._clear_default(session)
            row.name = name
            row.rules = asdict(rules)
            row.is_default = is_default
            row.version = row.version + 1
            return UpdateOutcome.OK, PresetAdminRecord(
                row.id, name, rules, is_default, row.is_active
            )

    async def deactivate(self, preset_id: str) -> DeactivateOutcome:
        async with self._sessionmaker() as session, session.begin():
            row = await session.get(RulePreset, preset_id, with_for_update=True)
            if row is None:
                return DeactivateOutcome.NOT_FOUND
            if row.is_default:
                # "Exactly one default" is a database constraint in one
                # direction only (at most one); "never zero" is here.
                return DeactivateOutcome.IS_DEFAULT
            row.is_active = False
            return DeactivateOutcome.OK

    @staticmethod
    async def _clear_default(session: AsyncSession) -> None:
        """Demote inside the same transaction as the promotion.

        `uq_rule_presets_single_default` is a partial unique index, so two
        rows with `is_default` cannot coexist even momentarily — doing this
        in a second transaction would fail half the time and corrupt the
        invariant the other half.
        """
        await session.execute(
            update(RulePreset).where(RulePreset.is_default).values(is_default=False)
        )
