"""The admin's read and write access to the question bank.

Deliberately not part of `repositories/questions.py`. That module is the
draw path — one method, taken under `FOR SHARE`, inside the caller's
transaction — and its docstring is an argument about locking that an
admin CRUD surface would bury. The two share only the tables.
"""

from typing import Any

from sqlalchemy import Select, func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from triviador.db.models.content import Category, Question, QuestionChoice, QuestionNumeric
from triviador.services.admin import (
    ChoiceRecord,
    QuestionDetailRecord,
    QuestionFilters,
    QuestionPage,
    QuestionSummaryRecord,
)


def _apply(statement: Select[Any], filters: QuestionFilters) -> Select[Any]:
    if filters.kind is not None:
        statement = statement.where(Question.kind == filters.kind)
    if filters.category_id is not None:
        statement = statement.where(Question.category_id == filters.category_id)
    if filters.difficulty is not None:
        statement = statement.where(Question.difficulty == filters.difficulty)
    if filters.is_active is not None:
        statement = statement.where(Question.is_active.is_(filters.is_active))
    if filters.has_media is not None:
        statement = statement.where(
            Question.media_asset_id.is_not(None)
            if filters.has_media
            else Question.media_asset_id.is_(None)
        )
    if filters.search:
        # `lower(prompt) LIKE lower(:needle)`, matching the expression the
        # trigram index is built on (migration 0004). `autoescape` turns a
        # literal `%` or `_` in the admin's search box into a literal
        # match instead of a wildcard that returns the whole bank.
        statement = statement.where(
            func.lower(Question.prompt).contains(filters.search.lower(), autoescape=True)
        )
    return statement


class QuestionAdminRepository:
    """Implements `services.admin.QuestionAdminPort`."""

    def __init__(self, sessionmaker: async_sessionmaker[AsyncSession]) -> None:
        self._sessionmaker = sessionmaker

    async def list(self, filters: QuestionFilters, *, limit: int, offset: int) -> QuestionPage:
        async with self._sessionmaker() as session:
            rows = (
                await session.execute(
                    _apply(
                        select(Question, Category.slug).join(
                            Category, Category.id == Question.category_id
                        ),
                        filters,
                    )
                    # `id` breaks ties: two questions seeded in the same
                    # transaction share `created_at` to the microsecond,
                    # and an unstable sort makes page 2 skip and repeat
                    # rows nobody edited.
                    .order_by(Question.created_at.desc(), Question.id)
                    .limit(limit)
                    .offset(offset)
                )
            ).all()
            total = (
                await session.execute(
                    _apply(select(func.count()).select_from(Question), filters)
                )
            ).scalar_one()
        return QuestionPage(
            items=tuple(_summary(question, slug) for question, slug in rows), total=total
        )

    async def get(self, question_id: str) -> QuestionDetailRecord | None:
        async with self._sessionmaker() as session:
            row = (
                await session.execute(
                    select(Question, Category.slug)
                    .join(Category, Category.id == Question.category_id)
                    .where(Question.id == question_id)
                )
            ).one_or_none()
            if row is None:
                return None
            question, slug = row
            choices = (
                (
                    await session.execute(
                        select(QuestionChoice)
                        .where(QuestionChoice.question_id == question_id)
                        .order_by(QuestionChoice.idx)
                    )
                )
                .scalars()
                .all()
            )
            numeric = await session.get(QuestionNumeric, question_id)
        return QuestionDetailRecord(
            question_id=question.id,
            kind=question.kind,
            prompt=question.prompt,
            category_id=question.category_id,
            category_slug=slug,
            difficulty=question.difficulty,
            is_active=question.is_active,
            version=question.version,
            media_asset_id=question.media_asset_id,
            choices=(
                tuple(
                    ChoiceRecord(c.idx, c.text, c.is_correct, c.media_asset_id) for c in choices
                )
                if choices
                else None
            ),
            numeric_answer=numeric.correct_value if numeric is not None else None,
            unit=numeric.unit if numeric is not None else None,
        )


def _summary(question: Question, category_slug: str) -> QuestionSummaryRecord:
    return QuestionSummaryRecord(
        question_id=question.id,
        kind=question.kind,
        prompt=question.prompt,
        category_id=question.category_id,
        category_slug=category_slug,
        difficulty=question.difficulty,
        is_active=question.is_active,
        has_media=question.media_asset_id is not None,
        version=question.version,
        updated_at=question.updated_at,
    )
