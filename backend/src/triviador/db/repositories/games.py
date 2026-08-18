"""`GameRepository`: genesis creation, listing, and the abandoned-lobby /
startup-recovery queries (Spec 1B §5.6, §6.2).

Two things this module deliberately does *not* do:

- `create` does not go through `TransactionContext.append`. `append`'s
  optimistic check is `UPDATE games ... WHERE last_seq = :expected`, and at
  genesis there is no `games` row yet for that `UPDATE` to match. §6.2's
  `tx1` is a direct two-insert commit instead — `INSERT games` and
  `INSERT game_events (seq=1, 'game.created')` in one transaction. That
  transaction *is* the append for seq 1, so this does not reintroduce a
  second mutation path for the read model; it is the one and only path for
  the one row (`games`) that has to exist before `game_events` can.
- `create` does not insert a `game_players` row for the host. `PlayerJoined`
  goes through the runtime queue (Plan 4), because putting seat allocation
  on a second mutation path is exactly what §8.2 forbids — seat allocation
  is the logic Plan 2 had to repair, so a single copy of it matters. The
  cost is a crash window leaving a player-less lobby, which
  `find_empty_lobbies` exists to collect.

Every query below reads `games` (joined against `game_players` where the
policy needs a player count) directly, never an in-memory runtime registry:
the no-connections rule (§5.6) may already have unloaded a runtime, and a
resident scan would leave that row in the database forever.
"""

from dataclasses import asdict
from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from triviador.db.codec.codec import encode
from triviador.db.models.games import Game, GameEventRow, GamePlayer
from triviador.domain.game.events import GameCreated
from triviador.domain.game.rules import GameRules
from triviador.domain.ids import GameId, MapId, PlayerId
from triviador.services.ports import GameSummary

# Re-exported: `GameSummary` moved to `services/ports.py` (Plan 5, R-9) so
# `services/` owns the type, but `tests/db/test_game_repository.py` still
# imports it from here, and every other existing import site keeps working.
__all__ = ["GameRepository", "GameSummary"]


def _to_summary(game: Game, player_count: int) -> GameSummary:
    return GameSummary(
        game_id=GameId(game.id),
        map_id=MapId(game.map_id),
        host_id=PlayerId(game.host_id),
        status=game.status,
        max_players=game.rules["player_count"],
        player_count=player_count,
        created_at=game.created_at,
    )


class GameRepository:
    def __init__(self, sessionmaker: async_sessionmaker[AsyncSession]) -> None:
        self._sessionmaker = sessionmaker

    async def create(
        self,
        *,
        game_id: GameId,
        map_id: MapId,
        rules: GameRules,
        host_id: PlayerId,
        map_sha256: str,
        preset_id: str | None,
        operation_id: str,
    ) -> None:
        """§6.2's `tx1`, verbatim: `INSERT games (status='lobby', last_seq=1)`
        and `INSERT game_events (seq=1, 'game.created')`, and nothing else.

        Not routed through `TransactionContext.append` — see the module
        docstring. Both inserts share one transaction so a crash between
        them is impossible; the crash window §5.6 actually has to cover is
        the one between this method returning and the host's `PlayerJoined`
        landing through the runtime, which is exactly what
        `find_empty_lobbies` exists for.
        """
        event = GameCreated(map_id=map_id, rules=rules, host_id=host_id, map_sha256=map_sha256)
        wire_type, schema_version, payload = encode(event)

        async with self._sessionmaker() as session, session.begin():
            session.add(
                Game(
                    id=game_id,
                    map_id=map_id,
                    rules=asdict(rules),
                    preset_id=preset_id,
                    status="lobby",
                    host_id=host_id,
                    last_seq=1,
                )
            )
            session.add(
                GameEventRow(
                    game_id=game_id,
                    seq=1,
                    operation_id=operation_id,
                    type=wire_type,
                    schema_version=schema_version,
                    payload=payload,
                )
            )

    async def get_summary(self, game_id: GameId) -> GameSummary | None:
        async with self._sessionmaker() as session:
            result = await session.execute(
                select(Game, func.count(GamePlayer.user_id))
                .outerjoin(GamePlayer, GamePlayer.game_id == Game.id)
                .where(Game.id == game_id)
                .group_by(Game.id)
            )
            row = result.one_or_none()
        if row is None:
            return None
        game, player_count = row
        return _to_summary(game, player_count)

    async def list_joinable(self) -> tuple[GameSummary, ...]:
        """`GET /api/games`: LOBBY games with at least one seated player.

        §6.2's crash window is what the `JOIN` (not `OUTERJOIN`) excludes —
        a lobby with zero rows in `game_players` produces zero matched rows
        here and is silently absent from the result, never advertised to a
        client that could not join it.
        """
        async with self._sessionmaker() as session:
            result = await session.execute(
                select(Game, func.count(GamePlayer.user_id))
                .join(GamePlayer, GamePlayer.game_id == Game.id)
                .where(Game.status == "lobby")
                .group_by(Game.id)
                .order_by(Game.created_at)
            )
            rows = result.all()
        return tuple(_to_summary(game, player_count) for game, player_count in rows)

    async def find_empty_lobbies(self, *, created_before: datetime) -> tuple[GameId, ...]:
        """The reaper's first policy (§5.6): LOBBY, zero players, older than
        the caller's cutoff. `created_before` is a parameter, not a constant
        read here — the 5-minute value is Plan 4's reaper policy, not this
        repository's.
        """
        async with self._sessionmaker() as session:
            result = await session.execute(
                select(Game.id)
                .outerjoin(GamePlayer, GamePlayer.game_id == Game.id)
                .where(Game.status == "lobby", Game.created_at < created_before)
                .group_by(Game.id)
                .having(func.count(GamePlayer.user_id) == 0)
            )
            ids = result.scalars().all()
        return tuple(GameId(i) for i in ids)

    async def find_stale_lobbies(self, *, created_before: datetime) -> tuple[GameId, ...]:
        """The reaper's second policy (§5.6): LOBBY, older than the caller's
        cutoff, regardless of player count.

        Deliberately does not join `game_players` or filter on its count —
        that is exactly what distinguishes this from `find_empty_lobbies`
        and catches a lobby that filled up and was then simply never
        started.
        """
        async with self._sessionmaker() as session:
            result = await session.execute(
                select(Game.id).where(Game.status == "lobby", Game.created_at < created_before)
            )
            ids = result.scalars().all()
        return tuple(GameId(i) for i in ids)

    async def find_unfinished(self) -> tuple[GameId, ...]:
        """Startup recovery: games in EXPANSION or BATTLE. There is no
        `final` status to query — `FinalTiebreak` is a `Turn` variant inside
        BATTLE, not a phase of its own (§1.1)."""
        async with self._sessionmaker() as session:
            result = await session.execute(
                select(Game.id).where(Game.status.in_(("expansion", "battle")))
            )
            ids = result.scalars().all()
        return tuple(GameId(i) for i in ids)
