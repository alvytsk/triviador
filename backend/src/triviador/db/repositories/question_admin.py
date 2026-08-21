"""The admin's read and write access to the question bank.

Deliberately not part of `repositories/questions.py`. That module is the
draw path — one method, taken under `FOR SHARE`, inside the caller's
transaction — and its docstring is an argument about locking that an
admin CRUD surface would bury. The two share only the tables.
"""

from typing import Any, NoReturn
from uuid import uuid4

from sqlalchemy import Select, delete, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from triviador.db.models.content import Category, Question, QuestionChoice, QuestionNumeric
from triviador.db.repositories.questions import prompt_digest
from triviador.domain.questions.types import QuestionKind
from triviador.services.admin import (
    CategoryNotFound,
    ChoiceRecord,
    MediaAssetNotFound,
    QuestionDetailRecord,
    QuestionFilters,
    QuestionPage,
    QuestionSummaryRecord,
    QuestionWrite,
)

# The two `questions` foreign keys, by the constraint name PostgreSQL
# reports on a violation (migration 0001) — the only way `create`/`update`
# can tell "the category is gone" from "the media asset is gone" apart,
# since both surface as the same `IntegrityError` otherwise.
_FK_CATEGORY = "fk_questions_category_id_categories"
_FK_MEDIA_ASSET = "fk_questions_media_asset_id_media_assets"


def _raise_for_fk_violation(exc: IntegrityError, write: QuestionWrite) -> NoReturn:
    """Translate a `questions` foreign-key violation into the domain
    exception `api/http/admin/questions.py` knows how to answer 404 for.

    Not a check-then-insert: `media-gc` is a concurrent writer that can
    delete the row between a `SELECT` and this `INSERT`/`UPDATE`, so the
    only race-free place to catch this is the constraint violation itself.
    A constraint this function does not recognise is re-raised unchanged,
    so it still reaches the generic `IntegrityError` handler (503) rather
    than being swallowed as one of these two.

    `exc.orig` is SQLAlchemy's own `AsyncAdapt_asyncpg_dbapi.IntegrityError`
    — a wrapper it constructs itself (asyncpg.py's `_handle_exception`) —
    not the driver's `ForeignKeyViolationError`, so `constraint_name` is
    not an attribute of `exc.orig` itself. That wrapper is raised `from`
    the original asyncpg exception, though, which is what carries the
    name, so it is read off `exc.orig.__cause__` instead — confirmed
    against real PostgreSQL, not assumed from the library's docs.
    """
    constraint = getattr(getattr(exc.orig, "__cause__", None), "constraint_name", None)
    if constraint == _FK_CATEGORY:
        raise CategoryNotFound(write.category_id) from exc
    if constraint == _FK_MEDIA_ASSET:
        raise MediaAssetNotFound(write.media_asset_id) from exc
    raise exc


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


CHOICE_COUNT = 4


def _validate(write: QuestionWrite) -> None:
    """Shape only. The *route* validates types and lengths through Pydantic;
    this is the invariant that must hold no matter who calls — the importer
    (Task 8) reaches these methods without passing through a schema.

    The `is_finite()` check on `numeric_answer` is repeated here rather than
    left to `QuestionWriteRequest`'s own validator for exactly that reason:
    `Decimal("NaN")` and `Decimal("Infinity")` are both constructible in
    plain Python (and PostgreSQL's `NUMERIC` will happily store either), so
    a caller that builds `QuestionWrite` directly — the importer, by this
    module's own admission above — would otherwise write one into the bank
    with nothing here to stop it.
    """
    if write.kind == QuestionKind.MULTIPLE_CHOICE.value:
        choices = write.choices or ()
        if len(choices) != CHOICE_COUNT:
            raise ValueError("a multiple-choice question needs exactly four choices")
        if sum(1 for _, correct in choices if correct) != 1:
            raise ValueError("a multiple-choice question needs exactly one correct choice")
        if write.numeric_answer is not None or write.unit is not None:
            raise ValueError("a multiple-choice question carries no numeric answer")
    elif write.kind == QuestionKind.NUMERIC.value:
        if write.numeric_answer is None:
            raise ValueError("a numeric question needs an answer")
        if not write.numeric_answer.is_finite():
            raise ValueError("a numeric answer must be finite")
        if write.choices:
            raise ValueError("a numeric question carries no choices")
    else:
        raise ValueError(f"unknown question kind {write.kind!r}")


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

    async def create(self, write: QuestionWrite) -> QuestionDetailRecord:
        _validate(write)
        question_id = str(uuid4())
        try:
            async with self._sessionmaker() as session, session.begin():
                session.add(
                    Question(
                        id=question_id,
                        version=1,
                        kind=write.kind,
                        prompt=write.prompt,
                        category_id=write.category_id,
                        difficulty=write.difficulty,
                        media_asset_id=write.media_asset_id,
                        is_active=True,
                        prompt_hash=prompt_digest(write.prompt),
                    )
                )
                await session.flush()
                self._write_children(session, question_id, write)
        except IntegrityError as exc:
            _raise_for_fk_violation(exc, write)
        record = await self.get(question_id)
        assert record is not None  # inserted and committed above
        return record

    async def update(self, question_id: str, write: QuestionWrite) -> QuestionDetailRecord | None:
        """Every call bumps `version`.

        Unconditionally, and without comparing old to new: this method is
        only reachable for a semantic edit (`is_active` has its own
        method), and a "did anything really change?" comparison is exactly
        the optimisation that eventually decides a choice-only edit did
        not count. The bump is also the lock — see the module docstring of
        `repositories/questions.py`.
        """
        _validate(write)
        try:
            async with self._sessionmaker() as session, session.begin():
                row = await session.get(Question, question_id, with_for_update=True)
                if row is None:
                    return None
                row.kind = write.kind
                row.prompt = write.prompt
                row.prompt_hash = prompt_digest(write.prompt)
                row.category_id = write.category_id
                row.difficulty = write.difficulty
                row.media_asset_id = write.media_asset_id
                row.version = row.version + 1
                await session.execute(
                    delete(QuestionChoice).where(QuestionChoice.question_id == question_id)
                )
                await session.execute(
                    delete(QuestionNumeric).where(QuestionNumeric.question_id == question_id)
                )
                await session.flush()
                self._write_children(session, question_id, write)
        except IntegrityError as exc:
            _raise_for_fk_violation(exc, write)
        return await self.get(question_id)

    async def set_active(
        self, question_id: str, *, is_active: bool
    ) -> QuestionDetailRecord | None:
        """No version bump (Spec 1 §7). The `UPDATE` still takes a row lock
        on `questions`, so a deactivation cannot race a pool draw either —
        that part comes free from touching the parent row."""
        async with self._sessionmaker() as session, session.begin():
            row = await session.get(Question, question_id, with_for_update=True)
            if row is None:
                return None
            row.is_active = is_active
        return await self.get(question_id)

    async def duplicates_of(self, prompt: str, *, excluding: str | None = None) -> tuple[str, ...]:
        digest = prompt_digest(prompt)
        async with self._sessionmaker() as session:
            statement = select(Question.id).where(Question.prompt_hash == digest)
            if excluding is not None:
                statement = statement.where(Question.id != excluding)
            return tuple((await session.execute(statement)).scalars().all())

    async def existing_prompt_digests(self, digests: frozenset[str]) -> frozenset[str]:
        if not digests:
            return frozenset()
        async with self._sessionmaker() as session:
            rows = await session.execute(
                select(Question.prompt_hash).where(Question.prompt_hash.in_(digests))
            )
            return frozenset(rows.scalars().all())

    async def active_counts(self) -> dict[str, int]:
        """Active questions per kind. The same shape `seed-questions`
        prints, computed the same way — one query, grouped."""
        async with self._sessionmaker() as session:
            rows = await session.execute(
                select(Question.kind, func.count())
                .where(Question.is_active.is_(True))
                .group_by(Question.kind)
            )
            counts = {kind.value: 0 for kind in QuestionKind}
            for kind, count in rows.all():
                counts[kind] = count
            return counts

    @staticmethod
    def _write_children(session: AsyncSession, question_id: str, write: QuestionWrite) -> None:
        if write.kind == QuestionKind.NUMERIC.value:
            session.add(
                QuestionNumeric(
                    question_id=question_id,
                    correct_value=write.numeric_answer,
                    unit=write.unit,
                )
            )
            return
        for idx, (choice_text, is_correct) in enumerate(write.choices or ()):
            session.add(
                QuestionChoice(
                    question_id=question_id,
                    idx=idx,
                    text=choice_text,
                    is_correct=is_correct,
                    media_asset_id=None,
                )
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
