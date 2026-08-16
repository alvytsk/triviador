import asyncio
from logging.config import fileConfig

from alembic import context
from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config

# triviador.db.models is imported for its side effect of registering every
# table on Base.metadata before target_metadata is read below. A model
# missing from that package's __all__ would make autogenerate propose
# dropping its table.
import triviador.db.models  # noqa: F401
from triviador.config import get_settings
from triviador.db.base import Base

# this is the Alembic Config object, which provides
# access to the values within the .ini file in use.
config = context.config

# Interpret the config file for Python logging.
# This line sets up loggers basically.
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# alembic.ini deliberately carries no `sqlalchemy.url` (a committed URL is a
# footgun that eventually points a migration at the wrong database). The
# fallback here is `Settings().database_url`, which has no default of its
# own: `TRIVIADOR_DATABASE_URL` unset raises `ValidationError` instead of
# silently resolving to some other database, which is the loud failure we
# want for `alembic upgrade head` run without it. A caller (tests, or any
# other script building a Config programmatically) can still override this
# by calling `config.set_main_option("sqlalchemy.url", ...)` before invoking
# a command, bypassing `Settings` entirely.
if config.get_main_option("sqlalchemy.url") is None:
    config.set_main_option("sqlalchemy.url", get_settings().database_url)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode.

    This configures the context with just a URL
    and not an Engine, though an Engine is acceptable
    here as well.  By skipping the Engine creation
    we don't even need a DBAPI to be available.

    Calls to context.execute() here emit the given string to the
    script output.

    """
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
        compare_server_default=True,
    )

    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        compare_type=True,
        compare_server_default=True,
    )

    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    """In this scenario we need to create an Engine
    and associate a connection with the context.

    """

    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)

    await connectable.dispose()


def run_migrations_online() -> None:
    """Run migrations in 'online' mode."""

    asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
