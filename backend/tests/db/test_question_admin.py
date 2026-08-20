import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tests.db.conftest import _seed_category, _seed_mc_question, _seed_numeric_question
from triviador.db.repositories.question_admin import QuestionAdminRepository
from triviador.services.admin import QuestionFilters

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
