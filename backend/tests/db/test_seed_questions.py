"""The seeder, against real PostgreSQL — the only place the idempotency
claim can actually be checked, since it is a uniqueness property of rows."""

from decimal import Decimal

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from triviador.db.models.content import Question, QuestionChoice, QuestionNumeric
from triviador.db.repositories.questions import QuestionBank, QuestionSeeder, SeedQuestion
from triviador.domain.questions.types import Difficulty, QuestionBudget, QuestionKind

pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="session")]


def _numeric(prompt: str) -> SeedQuestion:
    return SeedQuestion(
        kind=QuestionKind.NUMERIC,
        category_slug="science",
        category_name="Science",
        difficulty=Difficulty.EASY,
        prompt=prompt,
        unit="°C",
        correct_value=Decimal("100"),
        choices=(),
        correct_index=None,
    )


def _choice(prompt: str) -> SeedQuestion:
    return SeedQuestion(
        kind=QuestionKind.MULTIPLE_CHOICE,
        category_slug="science",
        category_name="Science",
        difficulty=Difficulty.EASY,
        prompt=prompt,
        unit=None,
        correct_value=None,
        choices=("A", "B", "C", "D"),
        correct_index=2,
    )


async def test_seeding_twice_inserts_once(
    clean_db: None, sessions: async_sessionmaker[AsyncSession]
) -> None:
    async with sessions() as session, session.begin():
        assert await QuestionSeeder(session).ensure(_numeric("How hot?")) is True
    async with sessions() as session, session.begin():
        assert await QuestionSeeder(session).ensure(_numeric("how   hot?")) is False
    async with sessions() as session:
        assert await session.scalar(select(func.count()).select_from(Question)) == 1


async def test_a_numeric_question_gets_its_child_row(
    clean_db: None, sessions: async_sessionmaker[AsyncSession]
) -> None:
    async with sessions() as session, session.begin():
        await QuestionSeeder(session).ensure(_numeric("How hot?"))
    async with sessions() as session:
        row = await session.scalar(select(QuestionNumeric))
        assert row is not None
        assert row.correct_value == Decimal("100")
        assert row.unit == "°C"


async def test_a_choice_question_gets_four_choices_and_exactly_one_correct(
    clean_db: None, sessions: async_sessionmaker[AsyncSession]
) -> None:
    async with sessions() as session, session.begin():
        await QuestionSeeder(session).ensure(_choice("Which one?"))
    async with sessions() as session:
        rows = (await session.scalars(select(QuestionChoice))).all()
        assert len(rows) == 4
        assert [r.idx for r in sorted(rows, key=lambda r: r.idx)] == [0, 1, 2, 3]
        correct = [r for r in rows if r.is_correct]
        assert len(correct) == 1
        assert correct[0].idx == 2


async def test_one_category_is_shared_by_every_question_that_names_it(
    clean_db: None, sessions: async_sessionmaker[AsyncSession]
) -> None:
    async with sessions() as session, session.begin():
        seeder = QuestionSeeder(session)
        await seeder.ensure(_numeric("First?"))
        await seeder.ensure(_numeric("Second?"))
    async with sessions() as session:
        ids = set((await session.scalars(select(Question.category_id))).all())
        assert len(ids) == 1


async def test_active_counts_reports_per_kind(
    clean_db: None, sessions: async_sessionmaker[AsyncSession]
) -> None:
    async with sessions() as session, session.begin():
        seeder = QuestionSeeder(session)
        await seeder.ensure(_numeric("First?"))
        await seeder.ensure(_choice("Second?"))
        counts = await seeder.active_counts()
    assert counts[QuestionKind.NUMERIC] == 1
    assert counts[QuestionKind.MULTIPLE_CHOICE] == 1


async def test_a_seeded_bank_can_actually_be_drawn_from(
    clean_db: None, sessions: async_sessionmaker[AsyncSession]
) -> None:
    """The claim this whole task exists to make true: after seeding, a pool
    can be drawn. `QuestionBank._materialize` is strict about shape, so this
    fails loudly if the seeder writes a question with no child rows."""
    async with sessions() as session, session.begin():
        seeder = QuestionSeeder(session)
        for i in range(3):
            await seeder.ensure(_numeric(f"Numeric {i}?"))
            await seeder.ensure(_choice(f"Choice {i}?"))
    async with sessions() as session, session.begin():
        pool = await QuestionBank(session).select_pool(QuestionBudget(numeric=3, multiple_choice=3))
    assert len(pool.numeric) == 3
    assert len(pool.multiple_choice) == 3
