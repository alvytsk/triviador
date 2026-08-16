"""Low-level `game_events` table access, used by `TransactionContext`.

Split out of `unit_of_work.py` on purpose: this module knows how to turn
`GameEvent` instances into `game_events` rows and back, and how to query
them. `unit_of_work.py` knows what a transaction is, when the optimistic
check must run, and what the read model requires. Neither needs the other's
internals beyond the three functions below.

Nothing here opens a transaction or commits — every function takes the
caller's `AsyncSession` and trusts it to be inside one already.
"""

from collections.abc import Sequence

from sqlalchemy import Row, select
from sqlalchemy.ext.asyncio import AsyncSession

from triviador.db.codec.codec import encode
from triviador.db.models.games import GameEventRow
from triviador.domain.game.events import GameEvent
from triviador.domain.ids import GameId


def insert_event_rows(
    session: AsyncSession,
    game_id: GameId,
    expected_last_seq: int,
    events: Sequence[GameEvent],
    operation_id: str,
) -> None:
    """Stage one `GameEventRow` per event, `seq` starting right after
    `expected_last_seq`. Synchronous: `session.add` performs no I/O of its
    own, the INSERTs happen when the surrounding transaction flushes."""
    for offset, event in enumerate(events, start=expected_last_seq + 1):
        wire_type, version, payload = encode(event)
        session.add(
            GameEventRow(
                game_id=game_id,
                seq=offset,
                operation_id=operation_id,
                type=wire_type,
                schema_version=version,
                payload=payload,
            )
        )


async def select_events_ordered(session: AsyncSession, game_id: GameId) -> Sequence[GameEventRow]:
    result = await session.execute(
        select(GameEventRow).where(GameEventRow.game_id == game_id).order_by(GameEventRow.seq)
    )
    return result.scalars().all()


async def select_event_refs_for_operation(
    session: AsyncSession, game_id: GameId, operation_id: str
) -> Sequence[Row[tuple[int, str]]]:
    """§5.5's reconciliation query, verbatim: `seq` and `type`, ordered.

    Deliberately does not decode `payload` — reconciliation asks "did my
    batch commit?", and decoding rows a different code path may have
    written is both slower and a second chance to fail.
    """
    result = await session.execute(
        select(GameEventRow.seq, GameEventRow.type)
        .where(GameEventRow.game_id == game_id, GameEventRow.operation_id == operation_id)
        .order_by(GameEventRow.seq)
    )
    return result.all()
