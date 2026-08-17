"""`QuestionBank.select_pool`: pool selection under `FOR SHARE` (Spec 1B §5.3).

Two things matter more than any individual test here:

- The materialized `QuestionSnapshot` carries every field a game needs —
  prompt, category, difficulty, choices (with `media_asset_id`), numeric
  answer, unit, the question's own `media_asset_id` — with no ORM
  relationship left to lazily traverse after the selecting transaction ends.
- `FOR SHARE`, not `FOR UPDATE`: a second `SELECT ... FOR UPDATE` on a
  selected row must genuinely block until the first transaction commits, but
  a second concurrent *reader* (another `select_pool` call, the shape two
  concurrent `StartGame` commands on different games would take) must not
  block at all. Both lock tests use `wait_until_a_backend_is_blocked_on`, the
  `pg_locks` polling barrier shared with `test_event_store.py` via
  `conftest.py`, rather than a plain `asyncio.Event`, for the same reason
  documented on that helper: an event only orders when each side *starts*,
  not whether a conflicting statement has actually reached Postgres and
  begun blocking — Task 6 measured that gap directly (see the task report).
"""

import asyncio
from decimal import Decimal

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tests.db.conftest import wait_until_a_backend_is_blocked_on
from triviador.db.errors import InsufficientQuestions, MalformedQuestion
from triviador.db.models.auth import User
from triviador.db.models.content import (
    Category,
    MediaAsset,
    Question,
    QuestionChoice,
    QuestionNumeric,
)
from triviador.db.repositories.questions import QuestionBank
from triviador.domain.questions.types import QuestionBudget, QuestionKind

pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="session")]


# --------------------------------------------------------------------------
# seed helpers
# --------------------------------------------------------------------------


async def _seed_category(
    sessionmaker: async_sessionmaker[AsyncSession],
    category_id: str = "cat-1",
    *,
    slug: str = "general",
    name: str = "General",
) -> None:
    async with sessionmaker() as session:
        session.add(Category(id=category_id, slug=slug, name=name))
        await session.commit()


async def _seed_user(sessionmaker: async_sessionmaker[AsyncSession], user_id: str) -> None:
    async with sessionmaker() as session:
        session.add(
            User(
                id=user_id,
                username=user_id,
                password_hash="hash",
                display_name=user_id,
                role="admin",
            )
        )
        await session.commit()


async def _seed_media_asset(
    sessionmaker: async_sessionmaker[AsyncSession], asset_id: str, *, created_by: str
) -> None:
    async with sessionmaker() as session:
        session.add(
            MediaAsset(
                id=asset_id,
                mime_type="image/webp",
                width=100,
                height=100,
                byte_size=1234,
                storage_key=f"media/{asset_id}.webp",
                created_by=created_by,
            )
        )
        await session.commit()


async def _seed_mc_question(
    sessionmaker: async_sessionmaker[AsyncSession],
    question_id: str,
    *,
    category_id: str = "cat-1",
    is_active: bool = True,
    prompt: str = "prompt",
    difficulty: str = "easy",
    version: int = 1,
    media_asset_id: str | None = None,
    choices: tuple[tuple[str, bool, str | None], ...] = (
        ("A", False, None),
        ("B", True, None),
    ),
) -> None:
    async with sessionmaker() as session:
        session.add(
            Question(
                id=question_id,
                version=version,
                kind="multiple_choice",
                prompt=prompt,
                category_id=category_id,
                difficulty=difficulty,
                media_asset_id=media_asset_id,
                is_active=is_active,
                prompt_hash=f"hash-{question_id}",
            )
        )
        for idx, (choice_text, is_correct, choice_media_asset_id) in enumerate(choices):
            session.add(
                QuestionChoice(
                    question_id=question_id,
                    idx=idx,
                    text=choice_text,
                    is_correct=is_correct,
                    media_asset_id=choice_media_asset_id,
                )
            )
        await session.commit()


async def _seed_numeric_question(
    sessionmaker: async_sessionmaker[AsyncSession],
    question_id: str,
    *,
    category_id: str = "cat-1",
    is_active: bool = True,
    prompt: str = "how many?",
    difficulty: str = "medium",
    version: int = 1,
    correct_value: Decimal = Decimal("42.5"),
    unit: str | None = "km",
    with_numeric_row: bool = True,
) -> None:
    """`with_numeric_row=False` seeds the bare `questions` row with
    `kind='numeric'` but no matching `question_numeric` row — the malformed
    shape `_materialize` must catch (F4): a row that passes `_select_kind`'s
    count check but has no child row for `_materialize` to read."""
    async with sessionmaker() as session:
        session.add(
            Question(
                id=question_id,
                version=version,
                kind="numeric",
                prompt=prompt,
                category_id=category_id,
                difficulty=difficulty,
                is_active=is_active,
                prompt_hash=f"hash-{question_id}",
            )
        )
        if with_numeric_row:
            session.add(
                QuestionNumeric(question_id=question_id, correct_value=correct_value, unit=unit)
            )
        await session.commit()


# `wait_until_a_backend_is_blocked_on` lives in `tests/db/conftest.py`, shared
# with `test_event_store.py` — see its docstring for why polling `pg_locks`
# (scoped to a relation, not cluster-wide `pg_stat_activity`) is necessary and
# a plain `asyncio.Event` barrier is not. This module previously carried its
# own byte-identical copy of this helper despite already claiming to reuse it.


# --------------------------------------------------------------------------
# basic selection
# --------------------------------------------------------------------------


async def test_select_pool_returns_the_requested_counts(
    clean_db: None, sessions: async_sessionmaker[AsyncSession]
) -> None:
    await _seed_category(sessions)
    for i in range(3):
        await _seed_mc_question(sessions, f"mc-{i}")
    for i in range(3):
        await _seed_numeric_question(sessions, f"num-{i}")

    async with sessions() as session, session.begin():
        pool = await QuestionBank(session).select_pool(QuestionBudget(numeric=2, multiple_choice=2))

    assert len(pool.numeric) == 2
    assert len(pool.multiple_choice) == 2
    assert len({q.question_id for q in pool.numeric}) == 2, "no duplicate draws"
    assert len({q.question_id for q in pool.multiple_choice}) == 2, "no duplicate draws"
    assert all(q.kind == QuestionKind.NUMERIC for q in pool.numeric)
    assert all(q.kind == QuestionKind.MULTIPLE_CHOICE for q in pool.multiple_choice)


async def test_a_zero_budget_issues_no_query_for_that_kind(
    clean_db: None, sessions: async_sessionmaker[AsyncSession]
) -> None:
    await _seed_category(sessions)
    await _seed_mc_question(sessions, "mc-1")

    async with sessions() as session, session.begin():
        pool = await QuestionBank(session).select_pool(QuestionBudget(numeric=0, multiple_choice=1))

    assert pool.numeric == ()
    assert len(pool.multiple_choice) == 1


async def test_inactive_questions_are_never_selected(
    clean_db: None, sessions: async_sessionmaker[AsyncSession]
) -> None:
    await _seed_category(sessions)
    await _seed_numeric_question(sessions, "active-1", is_active=True)
    await _seed_numeric_question(sessions, "active-2", is_active=True)
    for i in range(3):
        await _seed_numeric_question(sessions, f"inactive-{i}", is_active=False)

    async with sessions() as session, session.begin():
        pool = await QuestionBank(session).select_pool(QuestionBudget(numeric=2, multiple_choice=0))
    assert {q.question_id for q in pool.numeric} == {"active-1", "active-2"}

    # Asking for more than the two active rows must fail even though five
    # rows of that kind physically exist in the table.
    async with sessions() as session, session.begin():
        with pytest.raises(InsufficientQuestions) as exc_info:
            await QuestionBank(session).select_pool(QuestionBudget(numeric=3, multiple_choice=0))
    assert exc_info.value.available == 2


# --------------------------------------------------------------------------
# fully materialized snapshots
# --------------------------------------------------------------------------


async def test_snapshots_are_fully_materialized(
    clean_db: None, sessions: async_sessionmaker[AsyncSession]
) -> None:
    """Every field populated, choices included, no lazy load possible: the
    session backing the query is closed and expunged before any snapshot
    field is read. `QuestionSnapshot` is a plain frozen dataclass built from
    values already fetched, not an ORM object — the assertions below would
    raise `DetachedInstanceError` (or worse, silently re-open a connection)
    if `select_pool` had instead handed back live ORM rows relying on lazy
    relationship access."""
    await _seed_category(sessions, "cat-1", slug="geo", name="Geography")
    await _seed_user(sessions, "admin-1")
    await _seed_media_asset(sessions, "asset-question", created_by="admin-1")
    await _seed_media_asset(sessions, "asset-choice", created_by="admin-1")
    await _seed_mc_question(
        sessions,
        "mc-1",
        category_id="cat-1",
        prompt="Which is the capital?",
        difficulty="hard",
        version=3,
        media_asset_id="asset-question",
        choices=(
            ("Paris", True, "asset-choice"),
            ("Lyon", False, None),
            ("Nice", False, None),
        ),
    )

    async with sessions() as session, session.begin():
        pool = await QuestionBank(session).select_pool(QuestionBudget(numeric=0, multiple_choice=1))
        session.expunge_all()

    assert len(pool.multiple_choice) == 1
    snapshot = pool.multiple_choice[0]

    assert snapshot.question_id == "mc-1"
    assert snapshot.version == 3
    assert snapshot.kind == QuestionKind.MULTIPLE_CHOICE
    assert snapshot.prompt == "Which is the capital?"
    assert snapshot.category.category_id == "cat-1"
    assert snapshot.category.slug == "geo"
    assert snapshot.category.name == "Geography"
    assert snapshot.difficulty.value == "hard"
    assert snapshot.media_asset_id == "asset-question"
    assert snapshot.numeric_answer is None
    assert snapshot.unit is None

    assert snapshot.choices is not None
    assert len(snapshot.choices) == 3
    by_idx = {c.idx: c for c in snapshot.choices}
    assert by_idx[0].text == "Paris"
    assert by_idx[0].is_correct is True
    assert by_idx[0].media_asset_id == "asset-choice"
    assert by_idx[1].text == "Lyon"
    assert by_idx[1].is_correct is False
    assert by_idx[1].media_asset_id is None
    assert by_idx[2].text == "Nice"
    assert snapshot.correct_choice_index() == 0


async def test_numeric_snapshot_carries_the_answer_and_unit(
    clean_db: None, sessions: async_sessionmaker[AsyncSession]
) -> None:
    await _seed_category(sessions)
    await _seed_numeric_question(
        sessions, "num-1", correct_value=Decimal("1234.56"), unit="meters", version=2
    )

    async with sessions() as session, session.begin():
        pool = await QuestionBank(session).select_pool(QuestionBudget(numeric=1, multiple_choice=0))
        session.expunge_all()

    snapshot = pool.numeric[0]
    assert snapshot.version == 2
    assert snapshot.numeric_answer == Decimal("1234.56")
    assert isinstance(snapshot.numeric_answer, Decimal), "must never be a float"
    assert snapshot.unit == "meters"
    assert snapshot.choices is None
    assert snapshot.media_asset_id is None


# --------------------------------------------------------------------------
# MalformedQuestion — a bank-data problem must fail before the game starts,
# not resurface mid-game out of a committed QuestionPoolDrawn event.
# --------------------------------------------------------------------------


async def test_a_multiple_choice_row_with_no_choices_raises_malformed_question(
    clean_db: None, sessions: async_sessionmaker[AsyncSession]
) -> None:
    """`_select_kind`'s only check counts `questions` rows, not their
    `question_choices` children, so a `multiple_choice` row with zero
    choices passes it silently. `_materialize` must catch this itself."""
    await _seed_category(sessions)
    await _seed_mc_question(sessions, "mc-empty", choices=())

    async with sessions() as session, session.begin():
        with pytest.raises(MalformedQuestion) as exc_info:
            await QuestionBank(session).select_pool(QuestionBudget(numeric=0, multiple_choice=1))

    assert exc_info.value.question_id == "mc-empty"
    assert exc_info.value.kind == QuestionKind.MULTIPLE_CHOICE


async def test_a_numeric_row_with_no_numeric_data_raises_malformed_question(
    clean_db: None, sessions: async_sessionmaker[AsyncSession]
) -> None:
    """The numeric analogue: a `numeric`-kind `questions` row with no
    matching `question_numeric` row."""
    await _seed_category(sessions)
    await _seed_numeric_question(sessions, "num-empty", with_numeric_row=False)

    async with sessions() as session, session.begin():
        with pytest.raises(MalformedQuestion) as exc_info:
            await QuestionBank(session).select_pool(QuestionBudget(numeric=1, multiple_choice=0))

    assert exc_info.value.question_id == "num-empty"
    assert exc_info.value.kind == QuestionKind.NUMERIC


# --------------------------------------------------------------------------
# InsufficientQuestions
# --------------------------------------------------------------------------


async def test_insufficient_questions_raises_with_the_shortfall(
    clean_db: None, sessions: async_sessionmaker[AsyncSession]
) -> None:
    await _seed_category(sessions)
    await _seed_mc_question(sessions, "mc-1")

    async with sessions() as session, session.begin():
        with pytest.raises(InsufficientQuestions) as exc_info:
            await QuestionBank(session).select_pool(QuestionBudget(numeric=0, multiple_choice=5))

    assert exc_info.value.kind == QuestionKind.MULTIPLE_CHOICE
    assert exc_info.value.required == 5
    assert exc_info.value.available == 1


async def test_insufficient_questions_when_none_exist(
    clean_db: None, sessions: async_sessionmaker[AsyncSession]
) -> None:
    async with sessions() as session, session.begin():
        with pytest.raises(InsufficientQuestions) as exc_info:
            await QuestionBank(session).select_pool(QuestionBudget(numeric=1, multiple_choice=0))

    assert exc_info.value.kind == QuestionKind.NUMERIC
    assert exc_info.value.required == 1
    assert exc_info.value.available == 0


# --------------------------------------------------------------------------
# locking: FOR SHARE blocks a writer, but not another reader
# --------------------------------------------------------------------------


async def test_selection_holds_a_share_lock_for_the_transaction(
    clean_db: None, sessions: async_sessionmaker[AsyncSession]
) -> None:
    """A second connection's `SELECT ... FOR UPDATE` on the row `select_pool`
    just selected must block until the first transaction commits. The first
    attempt holds its transaction open until a third connection observes,
    via `pg_locks`, that some backend is genuinely waiting on a lock against
    `questions` — at which point the second attempt's conflicting `FOR
    UPDATE` is provably in flight and blocked, not merely scheduled to run.
    `SET LOCAL lock_timeout` bounds that block instead of letting a stuck
    test hang forever; nothing here waits on wall-clock time."""
    await _seed_category(sessions)
    await _seed_mc_question(sessions, "mc-1")
    budget = QuestionBudget(numeric=0, multiple_choice=1)

    first_locked = asyncio.Event()

    async def first() -> None:
        async with sessions() as session, session.begin():
            await session.execute(text("SET LOCAL lock_timeout = '2s'"))
            await QuestionBank(session).select_pool(budget)
            first_locked.set()
            await wait_until_a_backend_is_blocked_on(sessions, "questions")
            # exiting the `async with` commits, releasing the FOR SHARE lock

    async def second() -> None:
        await first_locked.wait()
        async with sessions() as session, session.begin():
            await session.execute(text("SET LOCAL lock_timeout = '2s'"))
            await session.execute(
                text("SELECT id FROM questions WHERE id = :id FOR UPDATE"), {"id": "mc-1"}
            )

    results = await asyncio.gather(first(), second(), return_exceptions=True)
    assert results[0] is None, results
    assert results[1] is None, results


async def test_a_share_lock_does_not_block_another_reader(
    clean_db: None, sessions: async_sessionmaker[AsyncSession]
) -> None:
    """Two concurrent `select_pool` calls — the shape two concurrent
    `StartGame` commands on different games take — both drawing from the
    same single-row bank must both proceed. `second` runs under a
    deliberately tiny `lock_timeout`, and `first` does not commit until
    `second` has already finished: `first`'s `FOR SHARE` lock is
    provably still held for the entire time `second`'s own `FOR SHARE`
    query executes. If `select_pool` ever took `FOR UPDATE` instead, this
    is the test that would fail — `second` would hit the 1ms timeout and
    raise instead of completing. `asyncio.wait_for` bounds `first`'s wait so
    a regression fails fast rather than hanging the suite."""
    await _seed_category(sessions)
    await _seed_mc_question(sessions, "mc-1")
    budget = QuestionBudget(numeric=0, multiple_choice=1)

    first_holds_the_lock = asyncio.Event()
    second_finished = asyncio.Event()

    async def first() -> None:
        async with sessions() as session, session.begin():
            await session.execute(text("SET LOCAL lock_timeout = '2s'"))
            await QuestionBank(session).select_pool(budget)
            first_holds_the_lock.set()
            await asyncio.wait_for(second_finished.wait(), timeout=5.0)
            # exiting the `async with` commits, releasing the FOR SHARE lock

    async def second() -> None:
        await first_holds_the_lock.wait()
        async with sessions() as session, session.begin():
            await session.execute(text("SET LOCAL lock_timeout = '1ms'"))
            pool = await QuestionBank(session).select_pool(budget)
            assert len(pool.multiple_choice) == 1
        second_finished.set()

    results = await asyncio.gather(first(), second(), return_exceptions=True)
    assert results[0] is None, results
    assert results[1] is None, results
