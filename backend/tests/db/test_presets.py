"""The default preset, and the four ways a lookup can go."""

import json
from dataclasses import asdict

import pytest
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from triviador.db.models.presets import RulePreset
from triviador.db.repositories.presets import PresetRepository
from triviador.db.seed import DEFAULT_PRESET_RULES
from triviador.domain.game.rules import DEFAULT_RULES, validate_rules

pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="session")]


def test_the_frozen_seed_is_a_valid_ruleset() -> None:
    """No database needed. What migration 0002 writes must be loadable, or
    every fresh installation is one `POST /api/games` away from a 500."""
    from triviador.db.repositories.presets import _to_rules

    assert validate_rules(_to_rules(dict(DEFAULT_PRESET_RULES))) == ()


def test_the_frozen_seed_still_matches_todays_defaults() -> None:
    """A drift alarm, not a duplication check.

    Migration 0002 froze these numbers deliberately (see its docstring), so
    this test failing is not a bug — it means someone changed
    `DEFAULT_RULES` and now has to decide what existing installations
    should do about it. Write migration `000N` to update them, then update
    the literal here.

    Compared through a JSON round-trip rather than directly against
    `dataclasses.asdict`: `asdict` preserves `claims_by_rank` as a tuple
    `(2, 1, 0)` while the frozen literal holds a JSON list `[2, 1, 0]`, and
    `tuple != list` would fail this test for a reason that has nothing to do
    with drift. `DEFAULT_PRESET_RULES`'s entire job is to be JSON-serialized
    into a JSONB column, so JSON-normalized equality is exactly the property
    that matters here.
    """
    assert dict(DEFAULT_PRESET_RULES) == json.loads(json.dumps(asdict(DEFAULT_RULES)))


async def test_exactly_one_default_preset_exists(
    default_preset: None, sessions: async_sessionmaker[AsyncSession]
) -> None:
    preset = await PresetRepository(sessions).get_default()
    assert preset is not None and preset.preset_id == "default"
    async with sessions() as session:
        rows = await session.execute(select(RulePreset).where(RulePreset.is_default))
        assert len(rows.all()) == 1


async def test_a_preset_is_reachable_by_id(
    default_preset: None, sessions: async_sessionmaker[AsyncSession]
) -> None:
    assert (await PresetRepository(sessions).get("default")) is not None
    assert (await PresetRepository(sessions).get("nope")) is None


async def test_an_inactive_preset_is_invisible(
    default_preset: None, sessions: async_sessionmaker[AsyncSession]
) -> None:
    """§6.1's soft deactivation. A retired preset must not be selectable for
    a new game, while `games.preset_id` on historical rows still resolves."""
    async with sessions() as session, session.begin():
        await session.execute(update(RulePreset).values(is_active=False))
    assert await PresetRepository(sessions).get("default") is None
    assert await PresetRepository(sessions).get_default() is None


async def test_the_previous_test_did_not_leak_its_deactivation(
    default_preset: None, sessions: async_sessionmaker[AsyncSession]
) -> None:
    """Named for what it guards, because the failure it catches is
    order-dependent and therefore invisible in a normal run: if the fixture
    ever stops re-seeding, this is the test that says so."""
    assert await PresetRepository(sessions).get_default() is not None


async def test_rules_that_no_longer_validate_are_refused_rather_than_returned(
    default_preset: None, sessions: async_sessionmaker[AsyncSession]
) -> None:
    """A preset row is JSONB written by an admin screen and by migrations
    across versions. Returning a `GameRules` that `validate_rules` rejects
    would push the failure into `decide`, which quarantines a runtime — so
    it fails here, where the caller can still answer 409."""
    async with sessions() as session, session.begin():
        await session.execute(
            update(RulePreset).values(rules={**asdict(DEFAULT_RULES), "player_count": 99})
        )
    with pytest.raises(ValueError):
        await PresetRepository(sessions).get("default")
