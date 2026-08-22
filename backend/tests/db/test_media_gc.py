"""§10.4: an asset is collectable only when *neither* a question *nor* any
persisted question snapshot names it.

The second half is the one that is easy to get wrong and expensive to get
wrong: `QuestionPoolDrawn` embeds whole `QuestionSnapshot`s in
`game_events.payload`, two levels deep (`pool.multiple_choice[].
media_asset_id` and `...choices[].media_asset_id`). Deleting a blob a
finished game still names does not break the game today — it breaks the
picture on a replay, forever.
"""

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tests.db.conftest import (
    _seed_asset,
    _seed_category,
    _seed_event_with_pool,
    _seed_mc_question,
    _seed_user,
)
from triviador.db.repositories.media import MediaAssetRepository

pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="session")]


async def test_an_asset_referenced_by_a_question_is_not_collectable(
    sessions: async_sessionmaker[AsyncSession], clean_db: None
) -> None:
    await _seed_user(sessions, "admin-1")
    await _seed_category(sessions)
    repository = MediaAssetRepository(sessions)
    await _seed_asset(sessions, "a" * 64)
    await _seed_mc_question(sessions, "q-1", media_asset_id="a" * 64)
    assert [r.asset_id for r in await repository.unreferenced()] == []


async def test_an_asset_referenced_only_by_a_choice_is_not_collectable(
    sessions: async_sessionmaker[AsyncSession], clean_db: None
) -> None:
    await _seed_user(sessions, "admin-1")
    await _seed_category(sessions)
    await _seed_asset(sessions, "b" * 64)
    await _seed_mc_question(sessions, "q-2", choices=(("A", True, "b" * 64), ("B", False, None)))
    assert [r.asset_id for r in await MediaAssetRepository(sessions).unreferenced()] == []


async def test_an_asset_named_only_inside_a_stored_event_is_not_collectable(
    sessions: async_sessionmaker[AsyncSession], clean_db: None
) -> None:
    """The whole point of the two-way check. The question row is gone from
    the bank's active set — this asset is referenced by nothing anybody
    could edit — and it still must not be deleted."""
    await _seed_user(sessions, "admin-1")
    await _seed_asset(sessions, "c" * 64)
    await _seed_event_with_pool(sessions, media_asset_id="c" * 64)
    assert [r.asset_id for r in await MediaAssetRepository(sessions).unreferenced()] == []


async def test_an_asset_nothing_names_is_collectable(
    sessions: async_sessionmaker[AsyncSession], clean_db: None
) -> None:
    await _seed_user(sessions, "admin-1")
    await _seed_asset(sessions, "d" * 64)
    assert [r.asset_id for r in await MediaAssetRepository(sessions).unreferenced()] == ["d" * 64]


async def test_claiming_deletes_the_rows_and_returns_them(
    sessions: async_sessionmaker[AsyncSession], clean_db: None
) -> None:
    """Rows first: the caller deletes the objects afterwards, so a crash
    between the two leaves an orphan object (collectable) rather than a
    row pointing at a missing blob (not)."""
    await _seed_user(sessions, "admin-1")
    await _seed_asset(sessions, "e" * 64)
    repository = MediaAssetRepository(sessions)
    claimed = await repository.claim_unreferenced()
    assert [r.asset_id for r in claimed] == ["e" * 64]
    assert await repository.get("e" * 64) is None
    assert await repository.claim_unreferenced() == ()


async def test_a_question_attached_during_the_sweep_cannot_lose_its_asset(
    sessions: async_sessionmaker[AsyncSession], clean_db: None
) -> None:
    """The race Decision 9 names, against real PostgreSQL.

    The sweep holds `FOR UPDATE` on the `media_assets` row; inserting a
    `questions` row that references it takes `FOR KEY SHARE` on the same
    row, which conflicts. So the attach must *wait*, and once the sweep
    commits its delete, the attach fails on the foreign key — loudly —
    instead of succeeding and pointing at a blob that is gone.

    A third interleaving is possible too, and is exactly as safe: the
    attach's insert commits *while the sweep is blocked* on the lock, so
    the sweep's own `SELECT` (whose anti-join ran before the block, and is
    never rerun by the lock wait — see `claim_unreferenced`'s docstring)
    still calls the asset unreferenced, but its per-row `DELETE` then hits
    the now-committed foreign key and is swallowed by that row's
    `SAVEPOINT`. Here that shows up as `attached` succeeding *and*
    `claimed` coming back empty — the same outcome as the attach-wins
    case below, just reached by a different interleaving.
    """
    import asyncio

    from sqlalchemy.exc import IntegrityError

    await _seed_user(sessions, "admin-1")
    await _seed_category(sessions)
    await _seed_asset(sessions, "f" * 64)

    async def attach() -> None:
        await _seed_mc_question(sessions, "q-late", media_asset_id="f" * 64)

    claim_result, attached = await asyncio.gather(
        MediaAssetRepository(sessions).claim_unreferenced(),
        attach(),
        return_exceptions=True,
    )
    assert not isinstance(claim_result, BaseException)
    claimed = claim_result
    # Exactly one of the two wins; whichever it is, no question ends up
    # referencing a deleted asset.
    if isinstance(attached, IntegrityError):
        assert [r.asset_id for r in claimed] == ["f" * 64]
    else:
        assert claimed == ()
