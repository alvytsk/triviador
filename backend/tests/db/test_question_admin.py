from decimal import Decimal

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from tests.db.conftest import (
    _seed_asset,
    _seed_category,
    _seed_mc_question,
    _seed_numeric_question,
    _seed_user,
)
from triviador.db.repositories.question_admin import QuestionAdminRepository
from triviador.services.admin import (
    CategoryNotFound,
    MediaAssetNotFound,
    QuestionFilters,
    QuestionWrite,
)

pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="session")]


async def _bank(sessions: async_sessionmaker[AsyncSession]) -> None:
    await _seed_category(sessions)
    await _seed_category(sessions, "cat-2", slug="film", name="Film")
    await _seed_mc_question(sessions, "q-mc", prompt="Who painted the Velvet Revolution mural?")
    await _seed_numeric_question(sessions, "q-num", prompt="In which year did it begin?")
    await _seed_mc_question(sessions, "q-off", prompt="Retired question", is_active=False)


async def test_the_list_pages_and_reports_the_unpaged_total(
    sessions: async_sessionmaker[AsyncSession], clean_db: None
) -> None:
    await _bank(sessions)
    page = await QuestionAdminRepository(sessions).list(QuestionFilters(), limit=2, offset=0)
    assert len(page.items) == 2
    assert page.total == 3


async def test_search_is_a_case_insensitive_substring(
    sessions: async_sessionmaker[AsyncSession], clean_db: None
) -> None:
    await _bank(sessions)
    page = await QuestionAdminRepository(sessions).list(
        QuestionFilters(search="velvet"), limit=50, offset=0
    )
    assert [q.question_id for q in page.items] == ["q-mc"]


async def test_a_percent_in_the_search_is_a_literal_not_a_wildcard(
    sessions: async_sessionmaker[AsyncSession], clean_db: None
) -> None:
    """Without escaping, `%` matches everything and an admin searching for
    a question about percentages gets the whole bank back."""
    await _bank(sessions)
    page = await QuestionAdminRepository(sessions).list(
        QuestionFilters(search="%"), limit=50, offset=0
    )
    assert page.items == ()


@pytest.mark.parametrize(
    ("filters", "expected"),
    [
        (QuestionFilters(kind="numeric"), ["q-num"]),
        (QuestionFilters(is_active=False), ["q-off"]),
        (QuestionFilters(has_media=True), []),
        # `q-num`'s difficulty is `_seed_numeric_question`'s own default,
        # "medium" — different from `_seed_mc_question`'s "easy" default —
        # so this row is a real exclusion, not the whole bank coming back.
        (QuestionFilters(difficulty="easy"), ["q-mc", "q-off"]),
    ],
)
async def test_each_filter_narrows_the_list(
    sessions: async_sessionmaker[AsyncSession],
    clean_db: None,
    filters: QuestionFilters,
    expected: list[str],
) -> None:
    await _bank(sessions)
    page = await QuestionAdminRepository(sessions).list(filters, limit=50, offset=0)
    assert sorted(q.question_id for q in page.items) == sorted(expected)


async def test_get_returns_the_choices_and_the_numeric_answer(
    sessions: async_sessionmaker[AsyncSession], clean_db: None
) -> None:
    await _bank(sessions)
    repository = QuestionAdminRepository(sessions)
    mc = await repository.get("q-mc")
    numeric = await repository.get("q-num")
    assert mc is not None and numeric is not None
    assert [c.text for c in mc.choices or ()] == ["A", "B"]
    assert numeric.choices is None and numeric.numeric_answer is not None
    assert await repository.get("nope") is None


async def test_editing_a_choice_bumps_the_parent_version(
    sessions: async_sessionmaker[AsyncSession], clean_db: None
) -> None:
    """The invariant `QuestionBank`'s `FOR SHARE` rests on: a choice lives
    in `question_choices`, which the draw never locks, so an edit that did
    not touch `questions` would be invisible to the lock entirely."""
    await _bank(sessions)
    repository = QuestionAdminRepository(sessions)
    before = await repository.get("q-mc")
    assert before is not None
    await repository.update(
        "q-mc",
        QuestionWrite(
            kind="multiple_choice",
            prompt=before.prompt,
            category_id=before.category_id,
            difficulty=before.difficulty,
            media_asset_id=None,
            choices=(("A", False), ("B", False), ("C", True), ("D", False)),
            numeric_answer=None,
            unit=None,
        ),
    )
    after = await repository.get("q-mc")
    assert after is not None
    assert after.version == before.version + 1
    assert [(c.text, c.is_correct) for c in after.choices or ()] == [
        ("A", False), ("B", False), ("C", True), ("D", False)
    ]


async def test_deactivation_does_not_bump_the_version(
    sessions: async_sessionmaker[AsyncSession], clean_db: None
) -> None:
    """Spec 1 §7: `is_active` is not a semantic edit, and bumping here
    would make Spec 2 treat one question's statistics as two questions'."""
    await _bank(sessions)
    repository = QuestionAdminRepository(sessions)
    before = await repository.get("q-mc")
    await repository.set_active("q-mc", is_active=False)
    after = await repository.get("q-mc")
    assert after is not None and before is not None
    assert (after.is_active, after.version) == (False, before.version)


async def test_an_edit_cannot_slip_past_a_pool_draw_in_flight(
    engine: AsyncEngine, sessions: async_sessionmaker[AsyncSession], clean_db: None
) -> None:
    """Two transactions, one row.

    A draws the question under `FOR SHARE` — what `QuestionBank` does
    inside `StartGame`'s transaction. B then edits the same question. B
    must block until A commits, because the edit bumps `version`, which is
    an `UPDATE` on the locked row. If this test ever passes instantly, the
    write path has stopped touching `questions` and the lock protects
    nothing.
    """
    import asyncio

    from sqlalchemy import text

    await _bank(sessions)
    repository = QuestionAdminRepository(sessions)
    write = QuestionWrite(
        kind="multiple_choice",
        prompt="Edited while the pool was being drawn",
        category_id="cat-1",
        difficulty="easy",
        media_asset_id=None,
        choices=(("A", True), ("B", False), ("C", False), ("D", False)),
        numeric_answer=None,
        unit=None,
    )

    async with sessions() as drawing:
        async with drawing.begin():
            await drawing.execute(
                text("SELECT id FROM questions WHERE id = :id FOR SHARE"), {"id": "q-mc"}
            )
            with pytest.raises(TimeoutError):
                await asyncio.wait_for(repository.update("q-mc", write), timeout=1.0)
        # The share lock is released by the COMMIT above; the same edit now
        # completes, proving the timeout was the lock and not a deadlock or
        # a broken statement.
        updated = await asyncio.wait_for(repository.update("q-mc", write), timeout=5.0)
    assert updated is not None and updated.prompt == write.prompt


async def test_a_multiple_choice_question_needs_four_choices_and_one_correct(
    sessions: async_sessionmaker[AsyncSession], clean_db: None
) -> None:
    await _bank(sessions)
    repository = QuestionAdminRepository(sessions)
    with pytest.raises(ValueError, match="four"):
        await repository.create(
            QuestionWrite(
                kind="multiple_choice",
                prompt="Three is not four",
                category_id="cat-1",
                difficulty="easy",
                media_asset_id=None,
                choices=(("A", True), ("B", False), ("C", False)),
                numeric_answer=None,
                unit=None,
            ),
        )


async def test_a_non_finite_numeric_answer_is_rejected_even_off_the_schema(
    sessions: async_sessionmaker[AsyncSession], clean_db: None
) -> None:
    """`QuestionWriteRequest` catches `Decimal("NaN")`/`Decimal("Infinity")`
    with `is_finite()`, but the importer (Task 8) builds `QuestionWrite`
    directly and never passes through that schema — so the repository has
    to hold this invariant too, or a non-finite answer reaches PostgreSQL's
    `NUMERIC`, which stores it without complaint."""
    await _bank(sessions)
    repository = QuestionAdminRepository(sessions)
    with pytest.raises(ValueError, match="finite"):
        await repository.create(
            QuestionWrite(
                kind="numeric",
                prompt="How many, really?",
                category_id="cat-1",
                difficulty="easy",
                media_asset_id=None,
                choices=None,
                numeric_answer=Decimal("NaN"),
                unit=None,
            ),
        )


async def test_active_counts_groups_by_kind_and_ignores_retired_questions(
    sessions: async_sessionmaker[AsyncSession], clean_db: None
) -> None:
    """§10.6's coverage readout (Plan 7A Task 12) reads this bank half.
    `_bank` alone (one active MC, one active numeric, one retired MC)
    would leave both kinds at the same count by coincidence; a second MC
    question makes the two counts genuinely different, so a test that
    only checked the *set* of keys could not pass by accident."""
    await _bank(sessions)
    await _seed_mc_question(sessions, "q-mc-2", prompt="A second active multiple-choice question")
    counts = await QuestionAdminRepository(sessions).active_counts()
    assert counts == {"multiple_choice": 2, "numeric": 1}


def _numeric_write(*, category_id: str, media_asset_id: str | None) -> QuestionWrite:
    return QuestionWrite(
        kind="numeric",
        prompt="How many, exactly?",
        category_id=category_id,
        difficulty="easy",
        media_asset_id=media_asset_id,
        choices=None,
        numeric_answer=Decimal("1"),
        unit=None,
    )


async def test_create_raises_category_not_found_for_a_nonexistent_category(
    sessions: async_sessionmaker[AsyncSession], clean_db: None
) -> None:
    """Important #1: `questions.category_id`'s foreign key, caught rather
    than left to surface as a raw `IntegrityError` (503)."""
    await _bank(sessions)
    repository = QuestionAdminRepository(sessions)
    with pytest.raises(CategoryNotFound):
        await repository.create(_numeric_write(category_id="no-such-category", media_asset_id=None))


async def test_create_raises_media_asset_not_found_for_a_nonexistent_asset(
    sessions: async_sessionmaker[AsyncSession], clean_db: None
) -> None:
    await _bank(sessions)
    repository = QuestionAdminRepository(sessions)
    with pytest.raises(MediaAssetNotFound):
        await repository.create(_numeric_write(category_id="cat-1", media_asset_id="no-such-asset"))


async def test_update_raises_category_not_found_for_a_nonexistent_category(
    sessions: async_sessionmaker[AsyncSession], clean_db: None
) -> None:
    await _bank(sessions)
    repository = QuestionAdminRepository(sessions)
    with pytest.raises(CategoryNotFound):
        await repository.update(
            "q-mc", _numeric_write(category_id="no-such-category", media_asset_id=None)
        )


async def test_update_raises_media_asset_not_found_for_a_nonexistent_asset(
    sessions: async_sessionmaker[AsyncSession], clean_db: None
) -> None:
    await _bank(sessions)
    repository = QuestionAdminRepository(sessions)
    with pytest.raises(MediaAssetNotFound):
        await repository.update(
            "q-mc", _numeric_write(category_id="cat-1", media_asset_id="no-such-asset")
        )


async def test_a_tab_left_open_across_a_media_gc_sweep_gets_404_not_a_crash(
    sessions: async_sessionmaker[AsyncSession], clean_db: None
) -> None:
    """The concrete scenario Important #1 names: an asset is uploaded and
    referenced by nothing yet, `media-gc` sweeps it away because it is
    genuinely unreferenced, and only then does the editor tab that had it
    open all along try to attach it to a save."""
    await _bank(sessions)
    await _seed_user(sessions, "admin-1")
    await _seed_asset(sessions, "asset-1")
    repository = QuestionAdminRepository(sessions)
    created = await repository.create(_numeric_write(category_id="cat-1", media_asset_id=None))

    async with sessions() as db, db.begin():
        await db.execute(text("DELETE FROM media_assets WHERE id = :id"), {"id": "asset-1"})

    with pytest.raises(MediaAssetNotFound):
        await repository.update(
            created.question_id, _numeric_write(category_id="cat-1", media_asset_id="asset-1")
        )
