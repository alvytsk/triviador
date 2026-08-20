"""`QuestionBank`: draws a question pool under a share lock (Spec 1B §5.3).

`select_pool` is meant to run inside the *same* transaction Plan 4 later
appends the resulting `QuestionPoolDrawn` event in — a caller opens a
`UnitOfWork.begin()` block and constructs `QuestionBank(tx.session)`, exactly
the way `TransactionContext.session` is documented to support. That is what
makes the lock meaningful: the pool cannot be drawn against rows that change
before that transaction commits, because nothing else can commit a
conflicting change to a locked row in the meantime.

**Why `FOR SHARE`, and why locking only the parent `questions` row is
enough.** `FOR SHARE` blocks a concurrent writer (`FOR UPDATE`, `UPDATE`,
`DELETE`) on the selected rows but lets any number of concurrent readers —
other `StartGame` commands drawing from the same bank for other games —
proceed without blocking each other. Locking only `questions`, not also
`question_choices`/`question_numeric`, is sufficient *only because* the spec
mandates that every semantic edit to a question (prompt, choices, correct
answer, category, difficulty, media, unit) bumps `questions.version`, and a
`version` bump is an `UPDATE` on the `questions` row itself. That promotes
the version-bump rule from bookkeeping to a locking invariant: a would-be
admin write path that edited `question_choices` without going through the
row that bumps `version` would slip past this lock entirely, since
`question_choices`/`question_numeric` are never themselves locked here.
Enforcing that bump, and testing that an admin path can't skip it, belongs
to Plan 7's admin write paths — this module only relies on the invariant,
it does not enforce it.

**Why the snapshots are fully materialized.** Once a `QuestionPool` is drawn,
the game never reads the bank again — the pool becomes part of the event log
via `QuestionPoolDrawn`, and replay must reproduce the same questions forever.
So every `QuestionSnapshot` here is built from explicit, eagerly-executed
`SELECT`s against `categories`/`question_choices`/`question_numeric`, never
from an ORM relationship traversed lazily after the fact — an admin editing
or deactivating a source row after the draw must not be able to change a
game already in flight or corrupt a later replay.

**Why `_materialize` checks shape, not just count.** A `multiple_choice` row
with zero `question_choices` rows, or a `numeric` row with no
`question_numeric` row, passes `_select_kind`'s only check (`len(rows) <
required` counts `questions` rows, not their children) and would otherwise
be baked into a `QuestionPoolDrawn` event as a structurally invalid
snapshot — a bank-data problem that only surfaces later as a `ValueError`
when that question is resolved mid-game, on a durable log that reproduces
the same failure on every recovery replay. `_materialize` raises
`MalformedQuestion` for both shapes instead, while the draw is still inside
its transaction and the game is still in `LOBBY`.
"""

from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal
from uuid import uuid4

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from triviador.db.errors import InsufficientQuestions, MalformedQuestion
from triviador.db.models.content import Category, Question, QuestionChoice, QuestionNumeric
from triviador.domain.ids import CategoryId, MediaAssetId, QuestionId
from triviador.domain.questions.types import (
    CategorySnapshot,
    ChoiceSnapshot,
    Difficulty,
    QuestionBudget,
    QuestionKind,
    QuestionPool,
    QuestionSnapshot,
)
from triviador.imports.digest import prompt_digest

__all__ = [
    "InsufficientQuestions",
    "MalformedQuestion",
    "QuestionBank",
    "QuestionSeeder",
    "SeedQuestion",
    "prompt_digest",
]


class QuestionBank:
    """Wraps one `AsyncSession`, expected to belong to the caller's already-open
    transaction — the same pattern `TransactionContext` uses. `QuestionBank`
    never opens or commits a transaction itself."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def select_pool(self, budget: QuestionBudget) -> QuestionPool:
        numeric = await self._select_kind(QuestionKind.NUMERIC, budget.numeric)
        multiple_choice = await self._select_kind(
            QuestionKind.MULTIPLE_CHOICE, budget.multiple_choice
        )
        return QuestionPool(numeric=numeric, multiple_choice=multiple_choice)

    async def _select_kind(self, kind: QuestionKind, required: int) -> tuple[QuestionSnapshot, ...]:
        if required == 0:
            return ()

        # §5.3 verbatim: `WHERE is_active AND kind = :kind ORDER BY random()
        # LIMIT :n FOR SHARE`. `.with_for_update(read=True)` is SQLAlchemy's
        # spelling of `FOR SHARE` (`read=False`, the default, would be `FOR
        # UPDATE` — the exclusive lock this method must *not* take, since two
        # concurrent `StartGame` draws on different games must both proceed).
        result = await self._session.execute(
            select(Question)
            .where(Question.is_active.is_(True), Question.kind == kind.value)
            .order_by(func.random())
            .limit(required)
            .with_for_update(read=True)
        )
        rows = result.scalars().all()
        if len(rows) < required:
            raise InsufficientQuestions(kind=kind, required=required, available=len(rows))
        return await self._materialize(rows)

    async def _materialize(self, questions: Sequence[Question]) -> tuple[QuestionSnapshot, ...]:
        """Turn locked `Question` rows into fully populated `QuestionSnapshot`s
        with three explicit, eager `SELECT`s — never an ORM relationship,
        since `Question`/`QuestionChoice`/`QuestionNumeric`/`Category` declare
        none, so there is nothing here to lazily traverse."""
        question_ids = [q.id for q in questions]
        category_ids = {q.category_id for q in questions}

        categories = {
            c.id: c
            for c in (
                await self._session.execute(select(Category).where(Category.id.in_(category_ids)))
            ).scalars()
        }

        choices_by_question: dict[str, list[QuestionChoice]] = defaultdict(list)
        choice_rows = (
            await self._session.execute(
                select(QuestionChoice)
                .where(QuestionChoice.question_id.in_(question_ids))
                .order_by(QuestionChoice.question_id, QuestionChoice.idx)
            )
        ).scalars()
        for choice in choice_rows:
            choices_by_question[choice.question_id].append(choice)

        numeric_by_question = {
            n.question_id: n
            for n in (
                await self._session.execute(
                    select(QuestionNumeric).where(QuestionNumeric.question_id.in_(question_ids))
                )
            ).scalars()
        }

        snapshots = []
        for question in questions:
            category = categories[question.category_id]
            kind = QuestionKind(question.kind)
            choices = choices_by_question.get(question.id)
            numeric = numeric_by_question.get(question.id)
            if kind is QuestionKind.MULTIPLE_CHOICE and choices is None:
                raise MalformedQuestion(question_id=QuestionId(question.id), kind=kind)
            if kind is QuestionKind.NUMERIC and numeric is None:
                raise MalformedQuestion(question_id=QuestionId(question.id), kind=kind)
            snapshots.append(
                QuestionSnapshot(
                    question_id=QuestionId(question.id),
                    version=question.version,
                    kind=kind,
                    prompt=question.prompt,
                    category=CategorySnapshot(
                        category_id=CategoryId(category.id),
                        slug=category.slug,
                        name=category.name,
                    ),
                    difficulty=Difficulty(question.difficulty),
                    choices=(
                        tuple(
                            ChoiceSnapshot(
                                idx=choice.idx,
                                text=choice.text,
                                is_correct=choice.is_correct,
                                media_asset_id=(
                                    MediaAssetId(choice.media_asset_id)
                                    if choice.media_asset_id is not None
                                    else None
                                ),
                            )
                            for choice in choices
                        )
                        if choices is not None
                        else None
                    ),
                    numeric_answer=numeric.correct_value if numeric is not None else None,
                    unit=numeric.unit if numeric is not None else None,
                    media_asset_id=(
                        MediaAssetId(question.media_asset_id)
                        if question.media_asset_id is not None
                        else None
                    ),
                )
            )
        return tuple(snapshots)


# --- seeding -----------------------------------------------------------------
# Deliberately *not* the admin write path. Plan 7 owns question editing, and
# with it the rule that every semantic edit bumps `questions.version` — the
# invariant this module's `FOR SHARE` lock depends on. `QuestionSeeder` only
# ever inserts whole new questions, so there is no edit for that rule to
# govern, and it must stay that way: an `UPDATE` added here is Plan 7's admin
# path written in the wrong module and outside the rule that keeps the lock
# meaningful.


@dataclass(frozen=True)
class SeedQuestion:
    kind: QuestionKind
    category_slug: str
    category_name: str
    difficulty: Difficulty
    prompt: str
    unit: str | None
    correct_value: Decimal | None
    choices: tuple[str, ...]
    correct_index: int | None


class QuestionSeeder:
    """Wraps one `AsyncSession` belonging to the caller's open transaction,
    exactly as `QuestionBank` does. Never opens or commits one itself."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def ensure_category(self, slug: str, name: str) -> str:
        existing = await self._session.scalar(select(Category).where(Category.slug == slug))
        if existing is not None:
            return existing.id
        category = Category(id=str(uuid4()), slug=slug, name=name)
        self._session.add(category)
        await self._session.flush()
        return category.id

    async def ensure(self, question: SeedQuestion) -> bool:
        """True if it was inserted, False if the bank already had it."""
        digest = prompt_digest(question.prompt)
        if await self._session.scalar(select(Question.id).where(Question.prompt_hash == digest)):
            return False

        row = Question(
            id=str(uuid4()),
            version=1,
            kind=question.kind.value,
            prompt=question.prompt,
            category_id=await self.ensure_category(question.category_slug, question.category_name),
            difficulty=question.difficulty.value,
            media_asset_id=None,
            is_active=True,
            prompt_hash=digest,
        )
        self._session.add(row)
        await self._session.flush()

        if question.kind is QuestionKind.NUMERIC:
            self._session.add(
                QuestionNumeric(
                    question_id=row.id, correct_value=question.correct_value, unit=question.unit
                )
            )
        else:
            for idx, text in enumerate(question.choices):
                self._session.add(
                    QuestionChoice(
                        question_id=row.id,
                        idx=idx,
                        text=text,
                        is_correct=idx == question.correct_index,
                        media_asset_id=None,
                    )
                )
        return True

    async def active_counts(self) -> dict[QuestionKind, int]:
        rows = await self._session.execute(
            select(Question.kind, func.count())
            .where(Question.is_active.is_(True))
            .group_by(Question.kind)
        )
        counts = dict.fromkeys(QuestionKind, 0)
        for kind, count in rows.all():
            counts[QuestionKind(kind)] = count
        return counts
