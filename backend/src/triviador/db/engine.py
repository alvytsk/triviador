from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

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
