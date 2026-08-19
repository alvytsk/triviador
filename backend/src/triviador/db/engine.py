from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)


def create_engine(url: str, *, echo: bool = False) -> AsyncEngine:
    return create_async_engine(url, echo=echo, pool_pre_ping=True)


def sessionmaker_for(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    # expire_on_commit=False: the runtime reads ORM objects after the
    # transaction context exits (§5.2 resolves origins only then), and a lazy
    # refresh at that point would be I/O on a closed transaction.
    return async_sessionmaker(engine, expire_on_commit=False)


@asynccontextmanager
async def engine_for(url: str) -> AsyncIterator[AsyncEngine]:
    engine = create_engine(url)
    try:
        yield engine
    finally:
        await engine.dispose()


class EnginePing:
    """`DatabaseProbe` over a real engine. Non-throwing — see the port's
    own docstring in `services/ports.py`: a probe that raised would reach
    the 500 handler instead of the readiness checklist a caller asked for.
    """

    def __init__(self, engine: AsyncEngine) -> None:
        self._engine = engine

    async def ping(self) -> bool:
        try:
            async with self._engine.connect() as connection:
                await connection.execute(text("SELECT 1"))
        except SQLAlchemyError:
            return False
        return True
