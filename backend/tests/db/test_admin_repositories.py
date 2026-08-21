from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import text as sql
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tests.db.conftest import _seed_user
from triviador.db.repositories.auth import InviteRepository, UserAdminRepository, UserRepository
from triviador.db.repositories.categories import CategoryRepository
from triviador.db.repositories.imports import QuestionImportRepository
from triviador.db.security import token_digest
from triviador.domain.ids import UserId
from triviador.services.admin import (
    CategoryRecord,
    ImportedImage,
    ImportedQuestion,
    ImportStatus,
    SlugTaken,
)
from triviador.services.identity import RedeemOutcome, UserRole

pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="session")]


async def test_a_duplicate_slug_raises_slug_taken(
    clean_db: None, sessions: async_sessionmaker[AsyncSession]
) -> None:
    repository = CategoryRepository(sessions)
    await repository.create(slug="film", name="Film")
    with pytest.raises(SlugTaken):
        await repository.create(slug="film", name="Cinema")


async def test_rename_leaves_the_slug_alone(
    clean_db: None, sessions: async_sessionmaker[AsyncSession]
) -> None:
    repository = CategoryRepository(sessions)
    created = await repository.create(slug="sport", name="Sport")
    renamed = await repository.rename(created.category_id, name="Sports")
    assert renamed == CategoryRecord(created.category_id, "sport", "Sports")


async def test_two_concurrent_confirms_cannot_both_apply(
    sessions: async_sessionmaker[AsyncSession], clean_db: None
) -> None:
    """§9.3: "the second loses at `FOR UPDATE` and returns 409". Asserted
    against real PostgreSQL, because the property is the lock's, not the
    code's."""
    import asyncio

    await _seed_user(sessions, "admin-1")
    repository = QuestionImportRepository(sessions)
    record = await repository.create(
        import_id="imp-1",
        uploaded_by="admin-1",
        upload_sha256="sha",
        filename="b.csv",
        staged_key="imp-1/b.csv",
        row_count=1,
        rejected_count=0,
        report={"rejections": []},
        expires_at=datetime.now(UTC) + timedelta(hours=1),
    )
    assert record.status is ImportStatus.VALIDATED

    async def apply() -> bool:
        return await repository.apply_if_confirmable(
            "imp-1",
            rows=(),
            images={},
            uploaded_by="admin-1",
            now=datetime.now(UTC),
        )

    first, second = await asyncio.gather(apply(), apply())
    assert sorted([first, second]) == [False, True]


async def test_two_confirms_introducing_the_same_new_category_do_not_race(
    sessions: async_sessionmaker[AsyncSession], clean_db: None
) -> None:
    """Important #2 (M4): `_ensure_categories` used to be
    `SELECT`-then-`INSERT`, with no conflict handling — unlike its
    neighbour `_ensure_assets`, which upserts. Two *different* imports
    (different `question_imports` rows, so `apply_if_confirmable`'s `FOR
    UPDATE` does not serialise them against each other) both introducing
    "sports" for the first time used to race a plain `INSERT` into a
    `UniqueViolation` on `categories.slug` — the same spurious 503
    Important #1 fixes for `questions`' foreign keys. Asserted against
    real PostgreSQL, because the property is the constraint's, not the
    code's: before the fix, this test fails with an unhandled
    `IntegrityError` on whichever side loses the race, not with the clean
    `[True, True]` it asserts below.
    """
    import asyncio

    await _seed_user(sessions, "admin-1")
    repository = QuestionImportRepository(sessions)
    for import_id in ("imp-a", "imp-b"):
        await repository.create(
            import_id=import_id,
            uploaded_by="admin-1",
            upload_sha256=f"sha-{import_id}",
            filename="b.csv",
            staged_key=f"{import_id}/b.csv",
            row_count=1,
            rejected_count=0,
            report={"rejections": [], "notices": []},
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        )

    def row(prompt: str) -> ImportedQuestion:
        return ImportedQuestion(
            category_slug="sports",
            kind="numeric",
            prompt=prompt,
            difficulty="easy",
            media_file=None,
            choices=None,
            numeric_answer=Decimal("1"),
            unit=None,
        )

    async def apply(import_id: str, prompt: str) -> bool:
        return await repository.apply_if_confirmable(
            import_id,
            rows=(row(prompt),),
            images={},
            uploaded_by="admin-1",
            now=datetime.now(UTC),
        )

    first, second = await asyncio.gather(
        apply("imp-a", "How many players does a game seat?"),
        apply("imp-b", "How many rounds does expansion run?"),
    )
    assert (first, second) == (True, True)

    async with sessions() as session:
        categories = (
            await session.execute(sql("SELECT id FROM categories WHERE slug = 'sports'"))
        ).scalars().all()
        category_ids = (
            await session.execute(
                sql(
                    "SELECT DISTINCT category_id FROM questions "
                    "WHERE prompt IN (:p1, :p2)"
                ),
                {
                    "p1": "How many players does a game seat?",
                    "p2": "How many rounds does expansion run?",
                },
            )
        ).scalars().all()
    # Exactly one "sports" row was ever created, and both questions —
    # written from two different transactions — ended up pointing at it.
    assert len(categories) == 1
    assert category_ids == [categories[0]]


async def test_two_admins_promoting_different_presets_do_not_leak_a_503(
    clean_db: None,
    default_preset: None,
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    """Reproduced live against `backend-postgres-test-1`. Two admins each
    promote a DIFFERENT non-default preset to default. Neither request is
    wrong on its own — each holds `FOR UPDATE` on its own target row, so
    they never block each other there — but both race `_clear_default`'s
    unconditional `UPDATE ... WHERE is_default = true`. The loser blocks,
    then correctly finds nothing left to clear once the winner commits,
    and its own `is_default = True` then collides with the winner's row
    on `uq_rule_presets_single_default`.

    Before the fix, that raw `IntegrityError` propagates straight out of
    `update()` — this test fails with an unhandled `IntegrityError` on
    whichever side loses, not with the clean `["lost_default_race", "ok"]`
    it asserts below. (The API layer would turn that same unhandled error
    into a misleading 503 `database_unavailable` — see
    `errors.py`'s `SQLAlchemyError` handler — for a request that was
    never actually wrong.) After the fix, the loser gets the typed
    `LOST_DEFAULT_RACE` outcome, and the database still ends up with
    exactly one default: the invariant was never actually broken, only
    misreported.
    """
    import asyncio

    from sqlalchemy import text as sql

    from triviador.db.models.presets import RulePreset
    from triviador.db.repositories.presets import PresetRepository
    from triviador.db.seed import DEFAULT_PRESET_RULES
    from triviador.domain.game.rules import DEFAULT_RULES

    async with sessions() as session, session.begin():
        session.add_all(
            [
                RulePreset(
                    id="preset-a",
                    name="A",
                    is_default=False,
                    rules=dict(DEFAULT_PRESET_RULES),
                    version=1,
                    is_active=True,
                ),
                RulePreset(
                    id="preset-b",
                    name="B",
                    is_default=False,
                    rules=dict(DEFAULT_PRESET_RULES),
                    version=1,
                    is_active=True,
                ),
            ]
        )

    repository = PresetRepository(sessions)

    async def promote(preset_id: str) -> str:
        outcome, _ = await repository.update(
            preset_id, name=preset_id, rules=DEFAULT_RULES, is_default=True
        )
        return outcome.value

    # A bare `asyncio.gather` of the two promotions is not enough to force
    # the race reliably: both transactions' first lock (`FOR UPDATE` on
    # their own, *different* target row) never contends, and the actual
    # point of contention — `_clear_default`'s `UPDATE ... WHERE
    # is_default = true` against the shared "default" row — is reached
    # late enough that one promotion can finish and commit before the
    # other's equivalent statement is even issued, over a socket this
    # fast. So a third connection locks "default" first and holds it,
    # forcing both promotions to actually queue up behind the same lock
    # before it lets go — which is when they race each other for real.
    async with sessions() as holder, holder.begin():
        await holder.execute(sql("SELECT id FROM rule_presets WHERE id = 'default' FOR UPDATE"))
        task_a = asyncio.create_task(promote("preset-a"))
        task_b = asyncio.create_task(promote("preset-b"))
        await asyncio.sleep(0.3)
        # Still blocked on the lock this transaction is holding — proof
        # the two promotions are genuinely queued up together, not merely
        # scheduled.
        assert not task_a.done()
        assert not task_b.done()
    # `holder`'s COMMIT above releases "default"; both blocked UPDATEs
    # resolve against each other from here.
    first, second = await asyncio.gather(task_a, task_b)
    assert sorted([first, second]) == ["lost_default_race", "ok"]

    async with sessions() as session:
        defaults = (
            await session.execute(sql("SELECT id FROM rule_presets WHERE is_default = true"))
        ).scalars().all()
    # Exactly one default survives, and it is whichever of the two
    # promotions actually won — never the original seeded "default" row,
    # and never both.
    assert defaults in (["preset-a"], ["preset-b"])


async def test_confirm_writes_every_kind_of_row_in_one_transaction(
    sessions: async_sessionmaker[AsyncSession], clean_db: None
) -> None:
    """The composition this task actually adds, against the real schema.

    Every individual statement here is proven elsewhere — questions and
    their children in `test_question_admin.py`, the media upsert in
    `test_media_repository.py`, category-ensure-in-the-caller's-transaction
    in `test_seed_questions.py`. What has never run against PostgreSQL is
    all five landing *together* inside the locked transaction with the
    status flip. A column-name typo, an FK ordering mistake or a
    `Decimal`/`NUMERIC` mismatch in that composition would pass every fake
    and fail on the first real import.
    """
    from sqlalchemy import text as sql

    await _seed_user(sessions, "admin-1")
    repository = QuestionImportRepository(sessions)
    await repository.create(
        import_id="imp-write",
        uploaded_by="admin-1",
        upload_sha256="sha",
        filename="bank.zip",
        staged_key="imp-write/bank.zip",
        row_count=2,
        rejected_count=0,
        report={"rejections": [], "notices": []},
        expires_at=datetime.now(UTC) + timedelta(hours=1),
    )

    applied = await repository.apply_if_confirmable(
        "imp-write",
        rows=(
            ImportedQuestion(
                category_slug="geography",
                kind="multiple_choice",
                prompt="Which river runs through Prague?",
                difficulty="easy",
                media_file="river.png",
                choices=(("Vltava", True), ("Elbe", False), ("Morava", False), ("Ohře", False)),
                numeric_answer=None,
                unit=None,
            ),
            ImportedQuestion(
                category_slug="history",
                kind="numeric",
                prompt="In which year did the Velvet Revolution begin?",
                difficulty="easy",
                media_file=None,
                choices=None,
                numeric_answer=Decimal("1989"),
                unit=None,
            ),
        ),
        images={
            "river.png": ImportedImage(
                asset_id="c" * 64,
                mime_type="image/webp",
                width=800,
                height=400,
                byte_size=1234,
                storage_key="cc/ccc.webp",
            )
        },
        uploaded_by="admin-1",
        now=datetime.now(UTC),
    )
    assert applied is True

    async with sessions() as session:
        counts = {
            table: (await session.execute(sql(f"SELECT count(*) FROM {table}"))).scalar_one()
            for table in (
                "categories",
                "questions",
                "question_choices",
                "question_numeric",
                "media_assets",
            )
        }
        status = (
            await session.execute(
                sql("SELECT status FROM question_imports WHERE id = 'imp-write'")
            )
        ).scalar_one()
        answer = (
            await session.execute(sql("SELECT correct_value FROM question_numeric"))
        ).scalar_one()
        attached = (
            await session.execute(
                sql("SELECT media_asset_id FROM questions WHERE kind = 'multiple_choice'")
            )
        ).scalar_one()

    assert counts == {
        "categories": 2,
        "questions": 2,
        "question_choices": 4,
        "question_numeric": 1,
        "media_assets": 1,
    }
    assert status == "confirmed"
    assert answer == Decimal("1989")
    assert attached == "c" * 64


async def test_an_expired_import_cannot_be_applied_even_with_zero_rejections(
    sessions: async_sessionmaker[AsyncSession], clean_db: None
) -> None:
    """The check that belongs under the lock, not only in the route: an
    import whose TTL passed while the confirm was in flight must lose."""
    await _seed_user(sessions, "admin-1")
    repository = QuestionImportRepository(sessions)
    await repository.create(
        import_id="imp-2",
        uploaded_by="admin-1",
        upload_sha256="sha",
        filename="b.csv",
        staged_key="imp-2/b.csv",
        row_count=1,
        rejected_count=0,
        report={"rejections": [], "notices": []},
        expires_at=datetime.now(UTC) - timedelta(seconds=1),
    )
    assert not await repository.apply_if_confirmable(
        "imp-2", rows=(), images={}, uploaded_by="admin-1", now=datetime.now(UTC)
    )


async def _seed_import(
    repository: QuestionImportRepository,
    import_id: str,
    *,
    expires_at: datetime,
    staged_key: str | None = None,
) -> None:
    await repository.create(
        import_id=import_id,
        uploaded_by="admin-1",
        upload_sha256="sha",
        filename="b.csv",
        staged_key=staged_key or f"{import_id}/b.csv",
        row_count=1,
        rejected_count=0,
        report={"rejections": [], "notices": []},
        expires_at=expires_at,
    )


async def test_count_expirable_and_mark_expired_only_touch_validated_rows_past_their_ttl(
    sessions: async_sessionmaker[AsyncSession], clean_db: None
) -> None:
    """`imports/retire.py`'s SQL, against the real schema: neither method
    is exercised anywhere else — `test_retire.py` proves the state machine
    over `FakeImports`, never the `UPDATE ... RETURNING` this repository
    actually runs."""
    await _seed_user(sessions, "admin-1")
    now = datetime.now(UTC)
    repository = QuestionImportRepository(sessions)
    await _seed_import(repository, "imp-past", expires_at=now - timedelta(hours=1))
    await _seed_import(repository, "imp-future", expires_at=now + timedelta(hours=1))
    await repository.create(
        import_id="imp-confirmed",
        uploaded_by="admin-1",
        upload_sha256="sha",
        filename="c.csv",
        staged_key="imp-confirmed/c.csv",
        row_count=1,
        rejected_count=0,
        report={"rejections": [], "notices": []},
        expires_at=now + timedelta(hours=1),
    )
    assert await repository.apply_if_confirmable(
        "imp-confirmed", rows=(), images={}, uploaded_by="admin-1", now=now
    ) is True

    assert await repository.count_expirable(now, all_unconfirmed=False) == 1
    assert await repository.mark_expired(now, all_unconfirmed=False) == 1

    past = await repository.get("imp-past")
    future = await repository.get("imp-future")
    confirmed = await repository.get("imp-confirmed")
    assert past is not None and past.status is ImportStatus.EXPIRED
    assert future is not None and future.status is ImportStatus.VALIDATED
    assert confirmed is not None and confirmed.status is ImportStatus.CONFIRMED


async def test_all_unconfirmed_expires_every_validated_row_regardless_of_ttl(
    sessions: async_sessionmaker[AsyncSession], clean_db: None
) -> None:
    """`--after-restore`: staging is not backed up (§10.9), so every
    `validated` row survives a restore already unconfirmable, whatever its
    own `expires_at` says."""
    await _seed_user(sessions, "admin-1")
    now = datetime.now(UTC)
    repository = QuestionImportRepository(sessions)
    await _seed_import(repository, "imp-past", expires_at=now - timedelta(hours=1))
    await _seed_import(repository, "imp-future", expires_at=now + timedelta(days=7))

    assert await repository.count_expirable(now, all_unconfirmed=True) == 2
    assert await repository.mark_expired(now, all_unconfirmed=True) == 2

    past = await repository.get("imp-past")
    future = await repository.get("imp-future")
    assert past is not None and past.status is ImportStatus.EXPIRED
    assert future is not None and future.status is ImportStatus.EXPIRED


async def test_retirable_staged_returns_expired_and_confirmed_rows_with_a_staged_key(
    sessions: async_sessionmaker[AsyncSession], clean_db: None
) -> None:
    """Neither a still-`validated` row nor an already-`cleaned` one (its
    `staged_key` already `NULL`) may show up here — that column is what
    makes the whole machine idempotent."""
    await _seed_user(sessions, "admin-1")
    now = datetime.now(UTC)
    repository = QuestionImportRepository(sessions)
    await _seed_import(repository, "imp-validated", expires_at=now + timedelta(hours=1))
    await _seed_import(repository, "imp-expired", expires_at=now - timedelta(hours=1))
    await repository.create(
        import_id="imp-confirmed",
        uploaded_by="admin-1",
        upload_sha256="sha",
        filename="c.csv",
        staged_key="imp-confirmed/c.csv",
        row_count=1,
        rejected_count=0,
        report={"rejections": [], "notices": []},
        expires_at=now + timedelta(hours=1),
    )
    assert await repository.apply_if_confirmable(
        "imp-confirmed", rows=(), images={}, uploaded_by="admin-1", now=now
    ) is True
    assert await repository.mark_expired(now, all_unconfirmed=False) == 1
    await repository.mark_cleaned("imp-expired")
    cleaned = await repository.get("imp-expired")
    assert cleaned is not None and cleaned.status is ImportStatus.CLEANED

    staged = dict(await repository.retirable_staged())
    assert staged == {"imp-confirmed": "imp-confirmed/c.csv"}


async def test_mark_cleaned_only_flips_expired_to_cleaned_and_always_clears_staged_key(
    sessions: async_sessionmaker[AsyncSession], clean_db: None
) -> None:
    """§9.3's third step: `confirmed` stays `confirmed` — that row is the
    audit trail — but loses its `staged_key` all the same."""
    await _seed_user(sessions, "admin-1")
    now = datetime.now(UTC)
    repository = QuestionImportRepository(sessions)
    await _seed_import(repository, "imp-expired", expires_at=now - timedelta(hours=1))
    await repository.mark_expired(now, all_unconfirmed=False)
    await repository.create(
        import_id="imp-confirmed",
        uploaded_by="admin-1",
        upload_sha256="sha",
        filename="c.csv",
        staged_key="imp-confirmed/c.csv",
        row_count=1,
        rejected_count=0,
        report={"rejections": [], "notices": []},
        expires_at=now + timedelta(hours=1),
    )
    assert await repository.apply_if_confirmable(
        "imp-confirmed", rows=(), images={}, uploaded_by="admin-1", now=now
    ) is True

    await repository.mark_cleaned("imp-expired")
    await repository.mark_cleaned("imp-confirmed")
    await repository.mark_cleaned("no-such-row")  # a no-op, not an error

    expired = await repository.get("imp-expired")
    confirmed = await repository.get("imp-confirmed")
    assert expired is not None
    assert expired.status is ImportStatus.CLEANED
    assert expired.staged_key is None
    assert confirmed is not None
    assert confirmed.status is ImportStatus.CONFIRMED
    assert confirmed.staged_key is None


async def test_issuing_n_codes_inserts_n_rows_each_with_its_own_digest(
    clean_db: None, sessions: async_sessionmaker[AsyncSession]
) -> None:
    """Against the real schema, not a fake: `invite_codes.code_hash` is
    UNIQUE (Plan 3), so a repository that ever computed the same digest
    twice would fail here with an `IntegrityError`, not silently pass."""
    await _seed_user(sessions, "admin-1")
    repository = InviteRepository(sessions)
    now = datetime.now(UTC)

    issued = await repository.issue(
        count=5, expires_at=now + timedelta(hours=1), created_by=UserId("admin-1")
    )
    assert len(issued) == 5
    assert len({invite_id for invite_id, _ in issued}) == 5
    assert len({code for _, code in issued}) == 5

    async with sessions() as session:
        rows = (await session.execute(sql("SELECT id, code_hash FROM invite_codes"))).all()
    assert len(rows) == 5
    stored = {(row.id, row.code_hash) for row in rows}
    # Every plaintext `issue` handed back hashes to exactly the digest its
    # own row stored in the database — not merely "5 distinct digests
    # exist somewhere", which a bug that stored the wrong pairing could
    # still satisfy.
    assert stored == {(invite_id, token_digest(code)) for invite_id, code in issued}


async def test_list_all_derives_every_status_from_rows_genuinely_in_that_state(
    clean_db: None, sessions: async_sessionmaker[AsyncSession]
) -> None:
    """Each of the four statuses, from a row that actually earned it: a
    real `redeem` for `used`, a real `revoke` for `revoked`, a past
    `expires_at` for `expired`, and an untouched row for `pending` — never
    a status column set directly, since there is no such column to set."""
    await _seed_user(sessions, "admin-1")
    repository = InviteRepository(sessions)
    now = datetime.now(UTC)

    [(pending_id, _)] = await repository.issue(
        count=1, expires_at=now + timedelta(hours=1), created_by=UserId("admin-1")
    )
    [(used_id, used_code)] = await repository.issue(
        count=1, expires_at=now + timedelta(hours=1), created_by=UserId("admin-1")
    )
    [(revoked_id, _)] = await repository.issue(
        count=1, expires_at=now + timedelta(hours=1), created_by=UserId("admin-1")
    )
    [(expired_id, _)] = await repository.issue(
        count=1, expires_at=now - timedelta(hours=1), created_by=UserId("admin-1")
    )

    outcome = await repository.redeem(
        code_hash=token_digest(used_code),
        user_id=UserId("new-player"),
        username="new-player",
        password_hash="hash",
        display_name="New Player",
        now=now,
    )
    assert outcome is RedeemOutcome.OK
    assert await repository.revoke(revoked_id, at=now) is True

    records = {record.invite_id: record for record in await repository.list_all(now=now)}
    assert records[pending_id].status == "pending"
    assert records[used_id].status == "used"
    assert records[used_id].used_by == "new-player"
    assert records[revoked_id].status == "revoked"
    assert records[expired_id].status == "expired"


async def test_revoking_twice_is_idempotent_at_the_database_level(
    clean_db: None, sessions: async_sessionmaker[AsyncSession]
) -> None:
    """The second revoke does not overwrite `revoked_at` with a later
    timestamp — an admin's retry must not be able to move the audited
    revocation time forward."""
    await _seed_user(sessions, "admin-1")
    repository = InviteRepository(sessions)
    now = datetime.now(UTC)
    [(invite_id, _)] = await repository.issue(
        count=1, expires_at=now + timedelta(hours=1), created_by=UserId("admin-1")
    )

    assert await repository.revoke(invite_id, at=now) is True
    later = now + timedelta(minutes=5)
    assert await repository.revoke(invite_id, at=later) is True

    async with sessions() as session:
        revoked_at = (
            await session.execute(
                sql("SELECT revoked_at FROM invite_codes WHERE id = :id"), {"id": invite_id}
            )
        ).scalar_one()
    assert revoked_at == now

    assert await repository.revoke("no-such-invite-id", at=now) is False


async def test_two_admins_cannot_demote_each_other_into_an_empty_room(
    clean_db: None, sessions: async_sessionmaker[AsyncSession]
) -> None:
    """The exact race §10.5 names. Both transactions see two admins if the
    check is a plain `SELECT count(*)`; the `FOR UPDATE` over every admin
    row serialises them, so the second sees one."""
    import asyncio

    repository = UserAdminRepository(sessions)
    await _seed_user(sessions, "a1")  # role='admin' (the helper's default)
    await _seed_user(sessions, "a2")

    outcomes = await asyncio.gather(
        repository.set_role(UserId("a1"), role=UserRole.PLAYER, at=datetime.now(UTC)),
        repository.set_role(UserId("a2"), role=UserRole.PLAYER, at=datetime.now(UTC)),
    )
    assert sorted(o.value for o, _ in outcomes) == ["last_admin", "ok"]
    assert await UserRepository(sessions).count_admins() == 1
