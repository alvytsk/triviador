"""`uv run triviador <command>`.

Three commands. `export-contracts` needs no database at all;
`admin-create` needs one, and is the bootstrap Spec 1 §10.1 specifies —
with its three outcomes spelled out so it is safe in a deployment script;
`seed-questions` needs one too, and installs the question bank Spec 1 §14.3
requires before `StartGame` can succeed.
"""

import argparse
import asyncio
import csv
import io
import random
import sys
import uuid
from decimal import Decimal, InvalidOperation
from enum import StrEnum
from pathlib import Path

from triviador.api.contracts import export_contracts
from triviador.config import get_settings
from triviador.db.engine import engine_for, sessionmaker_for
from triviador.db.repositories.auth import UserRepository
from triviador.db.repositories.presets import PresetRepository
from triviador.db.repositories.questions import QuestionSeeder, SeedQuestion, prompt_digest
from triviador.db.security import Argon2Hasher
from triviador.domain.game.rules import required_question_budget
from triviador.domain.ids import UserId
from triviador.domain.questions.types import Difficulty, QuestionKind
from triviador.services.identity import PasswordHasher, UserRole, UserStore


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

    args = parser.parse_args(argv)
    if args.command == "export-contracts":
        export_contracts(args.out)
        return 0
    if args.command == "seed-questions":
        return asyncio.run(_seed_questions_command(args))
    return asyncio.run(_admin_create_command(args))


if __name__ == "__main__":
    sys.exit(main())
