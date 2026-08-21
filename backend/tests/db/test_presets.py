"""The default preset, and the four ways a lookup can go."""

import json
from dataclasses import asdict

import pytest
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from triviador.db.models.presets import RulePreset
from triviador.db.repositories.presets import PresetRepository
from triviador.db.seed import DEFAULT_PRESET_RULES
from triviador.domain.game.rules import DEFAULT_RULES, GameRules, validate_rules
from triviador.services.admin import DeactivateOutcome, UpdateOutcome

# `integration` covers every test here; the asyncio mark is applied per async
# test rather than at module level. A module-level asyncio mark also lands on
# the two synchronous tests below, which have no loop to scope — pytest-asyncio
# warns about exactly that. `tests/db/conftest.py`'s collection guard already
# treats a sync item as exempt (`_lacks_session_loop_scope` is False when no
# marker is present), so stating the requirement per async test says the same
# thing where it is actually true.
pytestmark = [pytest.mark.integration]

session_loop = pytest.mark.asyncio(loop_scope="session")

# A second, distinguishable ruleset (Plan 7A Task 12's `QUICK`, mirrored
# here): shorter than `DEFAULT_RULES` in every round count, so a test that
# reads `rules` back can tell "the seed" from "what I just wrote" without
# comparing ids.
QUICK_RULES = GameRules(
    player_count=3,
    expansion_rounds=2,
    battle_rounds=2,
    base_hp=3,
    answer_timeout_ms=20_000,
    pick_timeout_ms=15_000,
    warmup_ms=5_000,
    claims_by_rank=(2, 1, 0),
    pts_base=1000,
    pts_territory=200,
    pts_conquered=400,
    pts_defense=100,
)


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


@session_loop
async def test_exactly_one_default_preset_exists(
    default_preset: None, sessions: async_sessionmaker[AsyncSession]
) -> None:
    preset = await PresetRepository(sessions).get_default()
    assert preset is not None and preset.preset_id == "default"
    async with sessions() as session:
        rows = await session.execute(select(RulePreset).where(RulePreset.is_default))
        assert len(rows.all()) == 1


@session_loop
async def test_a_preset_is_reachable_by_id(
    default_preset: None, sessions: async_sessionmaker[AsyncSession]
) -> None:
    assert (await PresetRepository(sessions).get("default")) is not None
    assert (await PresetRepository(sessions).get("nope")) is None


@session_loop
async def test_an_inactive_preset_is_invisible(
    default_preset: None, sessions: async_sessionmaker[AsyncSession]
) -> None:
    """§6.1's soft deactivation. A retired preset must not be selectable for
    a new game, while `games.preset_id` on historical rows still resolves."""
    async with sessions() as session, session.begin():
        await session.execute(update(RulePreset).values(is_active=False))
    assert await PresetRepository(sessions).get("default") is None
    assert await PresetRepository(sessions).get_default() is None


@session_loop
async def test_the_previous_test_did_not_leak_its_deactivation(
    default_preset: None, sessions: async_sessionmaker[AsyncSession]
) -> None:
    """Named for what it guards, because the failure it catches is
    order-dependent and therefore invisible in a normal run: if the fixture
    ever stops re-seeding, this is the test that says so."""
    assert await PresetRepository(sessions).get_default() is not None


@session_loop
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


# --- Task 12: CRUD, exercised against real PostgreSQL --------------------
#
# `uq_rule_presets_single_default` is a partial unique index (Plan 3): a
# fake could never catch a `create`/`update` that clears the old default
# in a *separate* transaction from the promotion, because nothing in an
# in-memory dict enforces "at most one" the way the database does. Every
# test below that touches `is_default` runs against the real constraint.


@session_loop
async def test_create_inserts_an_inactive_free_non_default_preset(
    default_preset: None, sessions: async_sessionmaker[AsyncSession]
) -> None:
    repository = PresetRepository(sessions)
    record = await repository.create(name="Quick", rules=QUICK_RULES, is_default=False)
    assert record.name == "Quick" and record.rules == QUICK_RULES
    assert not record.is_default and record.is_active
    # The seed default is untouched — a non-default `create` must not
    # reach `_clear_default` at all.
    default = await repository.get_default()
    assert default is not None and default.preset_id == "default"


@session_loop
async def test_creating_a_default_preset_demotes_the_previous_one_in_the_same_transaction(
    default_preset: None, sessions: async_sessionmaker[AsyncSession]
) -> None:
    """Without `_clear_default` running inside `create`'s own transaction,
    the insert below hits `uq_rule_presets_single_default` head-on: two
    rows with `is_default = true` cannot coexist even for the width of one
    statement."""
    repository = PresetRepository(sessions)
    created = await repository.create(name="Quick", rules=QUICK_RULES, is_default=True)
    async with sessions() as session:
        rows = (
            (await session.execute(select(RulePreset).where(RulePreset.is_default)))
            .scalars()
            .all()
        )
    assert [row.id for row in rows] == [created.preset_id]


@session_loop
async def test_list_active_excludes_a_retired_preset_that_list_all_still_shows(
    default_preset: None, sessions: async_sessionmaker[AsyncSession]
) -> None:
    repository = PresetRepository(sessions)
    created = await repository.create(name="Quick", rules=QUICK_RULES, is_default=False)
    assert (await repository.deactivate(created.preset_id)) is DeactivateOutcome.OK

    active = await repository.list_active()
    assert {r.name for r in active} == {"Default"}

    everyone = await repository.list_all()
    by_id = {r.preset_id: r.is_active for r in everyone}
    assert by_id == {"default": True, created.preset_id: False}


@session_loop
async def test_update_persists_new_rules_and_bumps_the_version(
    default_preset: None, sessions: async_sessionmaker[AsyncSession]
) -> None:
    repository = PresetRepository(sessions)
    outcome, record = await repository.update(
        "default", name="Renamed", rules=QUICK_RULES, is_default=True
    )
    assert outcome is UpdateOutcome.OK
    assert record is not None
    assert record.name == "Renamed" and record.rules == QUICK_RULES
    async with sessions() as session:
        row = await session.get(RulePreset, "default")
    assert row is not None and row.version == 2


@session_loop
async def test_update_refuses_to_clear_the_only_default(
    default_preset: None, sessions: async_sessionmaker[AsyncSession]
) -> None:
    """`uq_rule_presets_single_default` only forbids *two* defaults; a
    PATCH clearing the one default down to zero would sail past it
    entirely and leave `POST /api/games` answering `no_default_preset`
    for everyone. This refusal is application logic, proven here against
    the real table so a fake's looser bookkeeping cannot hide a bug in it.
    """
    repository = PresetRepository(sessions)
    outcome, record = await repository.update(
        "default", name="Default", rules=DEFAULT_RULES, is_default=False
    )
    assert outcome is UpdateOutcome.WOULD_LEAVE_NO_DEFAULT
    assert record is None
    default = await repository.get_default()
    assert default is not None and default.preset_id == "default"


@session_loop
async def test_update_refuses_to_promote_a_retired_preset(
    default_preset: None, sessions: async_sessionmaker[AsyncSession]
) -> None:
    """`get_default()` filters on `is_active`; promoting a retired row
    would make a default nothing can read — the same outage as having
    none, reached from the other side. Proven against the real row so the
    `is_active` check inside `update`'s transaction is what is actually
    exercised, not a fake's copy of the same rule."""
    repository = PresetRepository(sessions)
    created = await repository.create(name="Quick", rules=QUICK_RULES, is_default=False)
    assert (await repository.deactivate(created.preset_id)) is DeactivateOutcome.OK

    outcome, record = await repository.update(
        created.preset_id, name="Quick", rules=QUICK_RULES, is_default=True
    )
    assert outcome is UpdateOutcome.RETIRED_CANNOT_BE_DEFAULT
    assert record is None
    default = await repository.get_default()
    assert default is not None and default.preset_id == "default"


@session_loop
async def test_promoting_a_new_default_demotes_the_previous_one_in_the_same_transaction(
    default_preset: None, sessions: async_sessionmaker[AsyncSession]
) -> None:
    repository = PresetRepository(sessions)
    created = await repository.create(name="Quick", rules=QUICK_RULES, is_default=False)
    outcome, record = await repository.update(
        created.preset_id, name="Quick", rules=QUICK_RULES, is_default=True
    )
    assert outcome is UpdateOutcome.OK
    assert record is not None and record.is_default
    async with sessions() as session:
        rows = (
            (await session.execute(select(RulePreset).where(RulePreset.is_default)))
            .scalars()
            .all()
        )
    assert [row.id for row in rows] == [created.preset_id]


@session_loop
async def test_update_of_an_unknown_preset_is_not_found(
    default_preset: None, sessions: async_sessionmaker[AsyncSession]
) -> None:
    outcome, record = await PresetRepository(sessions).update(
        "nope", name="X", rules=QUICK_RULES, is_default=False
    )
    assert outcome is UpdateOutcome.NOT_FOUND
    assert record is None


@session_loop
async def test_deactivate_refuses_the_default(
    default_preset: None, sessions: async_sessionmaker[AsyncSession]
) -> None:
    outcome = await PresetRepository(sessions).deactivate("default")
    assert outcome is DeactivateOutcome.IS_DEFAULT
    assert await PresetRepository(sessions).get("default") is not None


@session_loop
async def test_deactivate_retires_a_non_default_preset(
    default_preset: None, sessions: async_sessionmaker[AsyncSession]
) -> None:
    repository = PresetRepository(sessions)
    created = await repository.create(name="Quick", rules=QUICK_RULES, is_default=False)
    assert (await repository.deactivate(created.preset_id)) is DeactivateOutcome.OK
    assert await repository.get(created.preset_id) is None
    active_ids = {r.preset_id for r in await repository.list_active()}
    assert created.preset_id not in active_ids


@session_loop
async def test_deactivate_of_an_unknown_preset_is_not_found(
    default_preset: None, sessions: async_sessionmaker[AsyncSession]
) -> None:
    outcome = await PresetRepository(sessions).deactivate("nope")
    assert outcome is DeactivateOutcome.NOT_FOUND
