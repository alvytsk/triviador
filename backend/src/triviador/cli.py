"""`uv run triviador <command>`.

Five commands. `export-contracts` needs no database at all;
`admin-create` needs one, and is the bootstrap Spec 1 §10.1 specifies —
with its three outcomes spelled out so it is safe in a deployment script;
`seed-questions` needs one too, and installs the question bank Spec 1 §14.3
requires before `StartGame` can succeed; `media-gc` needs a database and
both buckets, and is §9.3's expiry machine plus §10.4's asset sweep — rare
and destructive, so it is a command an operator runs, not a screen;
`migrate` needs one too, and is §10.1's `migrate` step — the dedicated,
non-restarting compose job that runs `alembic upgrade head` before `backend`
is allowed to start.
"""

import argparse
import asyncio
import csv
import fcntl
import io
import random
import sys
import uuid
from datetime import timedelta
from decimal import Decimal, InvalidOperation
from enum import StrEnum
from pathlib import Path

from alembic import command as alembic_command
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from triviador.api.contracts import export_contracts
from triviador.config import get_settings
from triviador.db.engine import engine_for, sessionmaker_for
from triviador.db.repositories.auth import UserRepository
from triviador.db.repositories.imports import QuestionImportRepository
from triviador.db.repositories.media import MediaAssetRepository
from triviador.db.repositories.presets import PresetRepository
from triviador.db.repositories.questions import QuestionSeeder, SeedQuestion, prompt_digest
from triviador.db.security import Argon2Hasher
from triviador.domain.game.rules import required_question_budget
from triviador.domain.ids import UserId
from triviador.domain.questions.types import Difficulty, QuestionKind
from triviador.imports.retire import ImportRetirer
from triviador.media.gc import MediaCollector
from triviador.media.lock import MEDIA_LOCK_PATH
from triviador.runtime.clock import SystemClock
from triviador.services.identity import PasswordHasher, UserRole, UserStore
from triviador.storage.s3 import S3ImportStagingStore, S3MediaStore


class AdminCreateOutcome(StrEnum):
    CREATED = "created"
    ALREADY_EXISTS = "already_exists"
    REFUSED = "refused"


async def admin_create(
    *,
    users: UserStore,
    hasher: PasswordHasher,
    username: str,
    password: str,
    display_name: str,
    force: bool,
) -> AdminCreateOutcome:
    """Spec 1 §10.1, exactly:

        no admins exist                       → create
        same username already exists as admin → success, no-op
        another admin already exists          → refuse unless --force

    The middle case is what makes this safe to run on every boot; the last
    is what stops a provisioning script from quietly minting admins.
    """
    existing = await users.get_by_username(username)
    if existing is not None:
        return (
            AdminCreateOutcome.ALREADY_EXISTS
            if existing.role is UserRole.ADMIN
            else AdminCreateOutcome.REFUSED
        )
    if await users.count_admins() > 0 and not force:
        return AdminCreateOutcome.REFUSED

    await users.create(
        user_id=UserId(uuid.uuid4().hex),
        username=username,
        password_hash=hasher.hash(password),
        display_name=display_name,
        role=UserRole.ADMIN,
    )
    return AdminCreateOutcome.CREATED


async def _admin_create_command(args: argparse.Namespace) -> int:
    settings = get_settings()
    async with engine_for(settings.database_url) as engine:
        outcome = await admin_create(
            users=UserRepository(sessionmaker_for(engine)),
            hasher=Argon2Hasher(),
            username=args.username,
            password=args.password,
            display_name=args.display_name or args.username,
            force=args.force,
        )
    print(outcome.value)
    return 1 if outcome is AdminCreateOutcome.REFUSED else 0


SEED_COLUMNS = (
    "kind",
    "category_slug",
    "category_name",
    "difficulty",
    "prompt",
    "unit",
    "answer",
    "choice_1",
    "choice_2",
    "choice_3",
    "choice_4",
    "correct_index",
)


def parse_seed_csv(text: str) -> tuple[SeedQuestion, ...]:
    """Every problem names its line, because a 32-row file with one bad cell
    is otherwise a `ValueError` pointing at nothing.

    Choices are shuffled by a seed derived from the prompt digest rather
    than kept in file order: authoring is easier when the correct answer is
    always written first, and a game in which it is always first is not a
    game. Deriving the seed from the prompt keeps the shuffle stable, which
    is what lets `seed-questions` stay idempotent.
    """
    reader = csv.DictReader(io.StringIO(text))
    if tuple(reader.fieldnames or ()) != SEED_COLUMNS:
        raise ValueError(f"header must be exactly {','.join(SEED_COLUMNS)}")

    questions: list[SeedQuestion] = []
    seen: set[str] = set()
    for line, row in enumerate(reader, start=2):
        try:
            question = _parse_seed_row(row)
        except ValueError as exc:
            raise ValueError(f"line {line}: {exc}") from exc
        digest = prompt_digest(question.prompt)
        if digest in seen:
            raise ValueError(f"line {line}: duplicate prompt")
        seen.add(digest)
        questions.append(question)
    return tuple(questions)


def _parse_seed_row(row: dict[str, str]) -> SeedQuestion:
    kind_raw = (row["kind"] or "").strip()
    if kind_raw not in {k.value for k in QuestionKind}:
        raise ValueError(f"unknown kind {kind_raw!r}")
    difficulty_raw = (row["difficulty"] or "").strip()
    if difficulty_raw not in {d.value for d in Difficulty}:
        raise ValueError(f"unknown difficulty {difficulty_raw!r}")
    prompt = (row["prompt"] or "").strip()
    if not prompt:
        raise ValueError("empty prompt")

    kind = QuestionKind(kind_raw)
    choices = tuple(
        c.strip() for c in (row[f"choice_{i}"] or "" for i in (1, 2, 3, 4)) if c.strip()
    )
    answer = (row["answer"] or "").strip()
    unit = (row["unit"] or "").strip() or None
    index_raw = (row["correct_index"] or "").strip()

    if kind is QuestionKind.NUMERIC:
        if choices or index_raw:
            raise ValueError("a numeric question must not carry choices")
        if not answer:
            raise ValueError("a numeric question needs an answer")
        try:
            value = Decimal(answer)
        except InvalidOperation as exc:
            raise ValueError(f"answer {answer!r} is not a decimal number") from exc
        if not value.is_finite():
            raise ValueError("answer must be finite")
        return SeedQuestion(
            kind,
            row["category_slug"].strip(),
            row["category_name"].strip(),
            Difficulty(difficulty_raw),
            prompt,
            unit,
            value,
            (),
            None,
        )

    if answer or unit:
        raise ValueError("a multiple-choice question must not carry answer or unit")
    if len(choices) < 3:
        raise ValueError(f"a multiple-choice question needs at least 3 choices, got {len(choices)}")
    if not index_raw.isdigit() or int(index_raw) >= len(choices):
        raise ValueError(f"correct_index {index_raw!r} is not one of {len(choices)} choices")

    ordered, correct = _shuffle_choices(choices, int(index_raw), prompt)
    return SeedQuestion(
        kind,
        row["category_slug"].strip(),
        row["category_name"].strip(),
        Difficulty(difficulty_raw),
        prompt,
        None,
        None,
        ordered,
        correct,
    )


def _shuffle_choices(
    choices: tuple[str, ...], correct: int, prompt: str
) -> tuple[tuple[str, ...], int]:
    rng = random.Random(prompt_digest(prompt))
    order = list(range(len(choices)))
    rng.shuffle(order)
    return tuple(choices[i] for i in order), order.index(correct)


async def _seed_questions_command(args: argparse.Namespace) -> int:
    questions = parse_seed_csv(args.csv.read_text(encoding="utf-8"))
    settings = get_settings()
    async with engine_for(settings.database_url) as engine:
        sessionmaker = sessionmaker_for(engine)
        async with sessionmaker() as session, session.begin():
            seeder = QuestionSeeder(session)
            inserted = sum([await seeder.ensure(q) for q in questions])
            counts = await seeder.active_counts()
        preset = await PresetRepository(sessionmaker).get_default()

    print(f"inserted {inserted}, unchanged {len(questions) - inserted}")
    for kind, count in sorted(counts.items()):
        print(f"active {kind.value}: {count}")
    if preset is None:
        print("no default preset: cannot check the question budget")
        return 0

    budget = required_question_budget(preset.rules)
    short = [
        f"{kind.value} needs {need}, bank has {counts[kind]}"
        for kind, need in (
            (QuestionKind.NUMERIC, budget.numeric),
            (QuestionKind.MULTIPLE_CHOICE, budget.multiple_choice),
        )
        if counts[kind] < need
    ]
    for line in short:
        print(f"SHORT: {line}")
    # Non-zero rather than a warning: a deployment script that seeds a bank
    # too small for its own default preset has produced a server on which
    # `StartGame` fails, and finding that out from a player is worse than
    # finding it out from the exit code.
    return 1 if short else 0


async def _media_gc_command(args: argparse.Namespace) -> int:
    """Rare and destructive, so it is a command and not a screen (§10.4) —
    and it prints what it did, because an operator running this at 2 a.m.
    needs to be able to tell "nothing to collect" from "did not run".

    Held under `MEDIA_LOCK_PATH` for the whole run — the identical host
    path `infra/backup.sh` flocks (see `triviador.media.lock`) — so this
    command's deletions can never interleave with a concurrent backup's
    `rclone copy`/`rclone check`. Without that exclusion, this command
    could delete an object a running backup has not copied yet, or delete
    one between the backup's copy and its verification, turning a healthy
    backup into a spurious failure.
    """
    settings = get_settings()
    MEDIA_LOCK_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(MEDIA_LOCK_PATH, "a") as lock_file:
        fcntl.flock(lock_file, fcntl.LOCK_EX)
        async with engine_for(settings.database_url) as engine:
            sessionmaker = sessionmaker_for(engine)
            staging = S3ImportStagingStore(
                endpoint_url=settings.s3_endpoint_url,
                region=settings.s3_region,
                access_key_id=settings.s3_access_key_id,
                secret_access_key=settings.s3_secret_access_key.get_secret_value(),
                bucket=settings.staging_bucket,
            )
            media = S3MediaStore(
                endpoint_url=settings.s3_endpoint_url,
                region=settings.s3_region,
                access_key_id=settings.s3_access_key_id,
                secret_access_key=settings.s3_secret_access_key.get_secret_value(),
                bucket=settings.media_bucket,
            )
            # Imports first: retiring a staged upload can only ever *reduce*
            # what the media sweep has to consider, and running the sweep
            # first would leave every just-expired object for the next run.
            clock = SystemClock()
            retired = await ImportRetirer(
                imports=QuestionImportRepository(sessionmaker),
                staging=staging,
                clock=clock,
            ).run(after_restore=args.after_restore, dry_run=args.dry_run)
            collected = await MediaCollector(
                assets=MediaAssetRepository(sessionmaker),
                store=media,
                grace=timedelta(minutes=settings.media_gc_grace_minutes),
            ).run(now=clock.now(), dry_run=args.dry_run)
        # Released here, at the end of the `with` block — the flock is
        # dropped automatically when the lock file descriptor closes.

    verb = "would expire" if args.dry_run else "expired"
    print(f"imports {verb} {retired.expired}, staged objects {retired.objects_deleted}")
    print(
        f"unreferenced assets {len(collected.unreferenced)}, "
        f"orphan objects {len(collected.orphan_objects)} "
        f"({collected.skipped_young} too recent to touch)"
    )
    if args.dry_run:
        print("dry run: nothing was deleted")
    return 0


# Arbitrary constant, scoped to this one lock: PostgreSQL advisory locks
# share a single 64-bit keyspace per database, and this is the only user of
# it. ADR-002 already guarantees exactly one running application process,
# so this lock is not protecting against concurrent replicas — there are
# none — it exists only to stop a human running `alembic upgrade` by hand
# from racing this command during a deploy.
_MIGRATE_LOCK_KEY = 7_198_402_331


def _alembic_ini() -> Path:
    """`alembic.ini` lives at the backend project root, not inside the
    installed package, so finding it via `Path(__file__)` breaks the moment
    the package is installed non-editable — exactly what
    `infra/backend.Dockerfile`'s `uv sync --no-editable` does, at which
    point `__file__` resolves somewhere under `site-packages`, nowhere near
    it.

    Both the container (`WORKDIR /app`) and every documented local
    invocation (`cd backend && uv run triviador ...`) instead run with the
    backend project root as the working directory, so that is what this
    resolves against.
    """
    path = Path.cwd() / "alembic.ini"
    if not path.is_file():
        raise FileNotFoundError(
            f"{path} not found: `triviador migrate` must be run with the backend "
            "project root (where alembic.ini lives) as the working directory"
        )
    return path


async def migrate_head(engine: AsyncEngine, database_url: str) -> None:
    """Run `alembic upgrade head` while holding a session-scoped PostgreSQL
    advisory lock, so a second, concurrent invocation blocks instead of
    racing this one (see `_MIGRATE_LOCK_KEY` for why that guard exists).

    The lock is acquired and released on the same connection throughout —
    `pg_advisory_lock` is tied to the backend session that took it, so
    letting the connection be returned to a pool between the lock and the
    unlock would silently drop it.
    """
    config = Config(str(_alembic_ini()))
    config.set_main_option("sqlalchemy.url", database_url)
    async with engine.connect() as conn:
        await conn.execute(text("SELECT pg_advisory_lock(:key)"), {"key": _MIGRATE_LOCK_KEY})
        try:
            # `command.upgrade` drives its own `asyncio.run(...)` inside
            # `env.py`, which cannot be invoked from within a running event
            # loop — so it runs on its own thread, same as the test suite's
            # `_run_upgrade_head`.
            await asyncio.to_thread(alembic_command.upgrade, config, "head")
        finally:
            await conn.execute(
                text("SELECT pg_advisory_unlock(:key)"), {"key": _MIGRATE_LOCK_KEY}
            )


async def _migrate_command(args: argparse.Namespace) -> int:
    settings = get_settings()
    async with engine_for(settings.database_url) as engine:
        await migrate_head(engine, settings.database_url)
    print("migrated to head")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="triviador")
    commands = parser.add_subparsers(dest="command", required=True)

    export = commands.add_parser("export-contracts")
    export.add_argument("--out", type=Path, required=True)

    admin = commands.add_parser("admin-create")
    admin.add_argument("--username", required=True)
    admin.add_argument(
        "--password",
        required=True,
        help=(
            "Spec 1 §10.1's stated interface. On the command line this "
            "lands in shell history and is visible to other processes via "
            "`ps` — prefer a leading space or a here-string when invoking "
            "this in a shell that supports it."
        ),
    )
    admin.add_argument("--display-name")
    admin.add_argument("--force", action="store_true")

    seed = commands.add_parser("seed-questions")
    seed.add_argument("--csv", type=Path, required=True)

    gc = commands.add_parser("media-gc")
    gc.add_argument("--dry-run", action="store_true")
    gc.add_argument(
        "--after-restore",
        action="store_true",
        help=(
            "expire every unconfirmed import regardless of its expiry: staging "
            "is not backed up (§10.9), so after a restore their uploads are gone"
        ),
    )

    commands.add_parser("migrate")

    args = parser.parse_args(argv)
    if args.command == "export-contracts":
        export_contracts(args.out)
        return 0
    if args.command == "seed-questions":
        return asyncio.run(_seed_questions_command(args))
    if args.command == "media-gc":
        return asyncio.run(_media_gc_command(args))
    if args.command == "migrate":
        return asyncio.run(_migrate_command(args))
    return asyncio.run(_admin_create_command(args))


if __name__ == "__main__":
    sys.exit(main())
