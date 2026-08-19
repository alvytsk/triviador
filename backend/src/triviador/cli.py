"""`uv run triviador <command>`.

Two commands. `export-contracts` needs no database at all;
`admin-create` needs one, and is the bootstrap Spec 1 §10.1 specifies —
with its three outcomes spelled out so it is safe in a deployment script.
"""

import argparse
import asyncio
import sys
import uuid
from enum import StrEnum
from pathlib import Path

from triviador.api.contracts import export_contracts
from triviador.config import get_settings
from triviador.db.engine import engine_for, sessionmaker_for
from triviador.db.repositories.auth import UserRepository
from triviador.db.security import Argon2Hasher
from triviador.domain.ids import UserId
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

    args = parser.parse_args(argv)
    if args.command == "export-contracts":
        export_contracts(args.out)
        return 0
    return asyncio.run(_admin_create_command(args))


if __name__ == "__main__":
    sys.exit(main())
