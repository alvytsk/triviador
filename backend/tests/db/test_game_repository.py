"""`GameRepository`: genesis creation (§6.2 tx1), listing, and the two
abandoned-lobby policies plus startup recovery (§5.6).

Age-based tests never sleep or depend on wall-clock timing: every game row's
`created_at` is set explicitly at seed time (overriding the `server_default`),
and every `created_before` cutoff passed to a query is a fixed instant
computed from that same seed value. The passage of real time during a test
run cannot change the outcome.
"""

from collections.abc import Sequence
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tests.conftest import grid_map
from triviador.db.models.auth import User
from triviador.db.models.games import Game, GameEventRow, GamePlayer
from triviador.db.repositories.games import GameRepository, GameSummary
from triviador.db.unit_of_work import UnitOfWork
from triviador.domain.game import events as ev
from triviador.domain.game.genesis import create_initial_state
from triviador.domain.game.rules import DEFAULT_RULES
from triviador.domain.game.state import Phase
from triviador.domain.ids import GameId, MapId, PlayerId

pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="session")]

NOW = datetime(2026, 8, 16, 12, 0, tzinfo=UTC)
MAP_SHA = "1" * 64


async def _seed_user(sessionmaker: async_sessionmaker[AsyncSession], user_id: str) -> None:
    async with sessionmaker() as session:
        session.add(
            User(
                id=user_id,
                username=user_id,
                password_hash="hash",
                display_name=user_id,
                role="player",
            )
        )
        await session.commit()


async def _seed_game_row(
    sessionmaker: async_sessionmaker[AsyncSession],
    game_id: str,
    *,
    host_id: str,
    status: str = "lobby",
    created_at: datetime = NOW,
    last_seq: int = 1,
) -> None:
    """Direct row insert, bypassing `GameRepository.create`, so the query
    tests can control `status` and `created_at` precisely without depending
    on the genesis path under test elsewhere."""
    async with sessionmaker() as session:
        session.add(
            Game(
                id=game_id,
                map_id="grid",
                rules={"player_count": 3},
                status=status,
                host_id=host_id,
                created_at=created_at,
                last_seq=last_seq,
            )
        )
        await session.commit()


async def _seed_player(
    sessionmaker: async_sessionmaker[AsyncSession], game_id: str, user_id: str, seat: int
) -> None:
    async with sessionmaker() as session:
        session.add(GamePlayer(game_id=game_id, user_id=user_id, seat=seat))
        await session.commit()


async def _event_rows(
    sessionmaker: async_sessionmaker[AsyncSession], game_id: str
) -> Sequence[GameEventRow]:
    async with sessionmaker() as session:
        result = await session.execute(
            select(GameEventRow).where(GameEventRow.game_id == game_id).order_by(GameEventRow.seq)
        )
        return result.scalars().all()


async def _game_players(
    sessionmaker: async_sessionmaker[AsyncSession], game_id: str
) -> Sequence[GamePlayer]:
    async with sessionmaker() as session:
        result = await session.execute(select(GamePlayer).where(GamePlayer.game_id == game_id))
        return result.scalars().all()


# --------------------------------------------------------------------------
# create — §6.2 tx1
# --------------------------------------------------------------------------


async def test_create_writes_exactly_one_event_and_one_game_row(
    clean_db: None, sessions: async_sessionmaker[AsyncSession]
) -> None:
    await _seed_user(sessions, "host")
    repo = GameRepository(sessions)

    await repo.create(
        game_id=GameId("g1"),
        map_id=MapId("grid"),
        rules=DEFAULT_RULES,
        host_id=PlayerId("host"),
        map_sha256=MAP_SHA,
        preset_id=None,
        operation_id="op-1",
    )

    async with sessions() as session:
        game = await session.get(Game, "g1")
    assert game is not None
    assert game.status == "lobby"
    assert game.last_seq == 1
    assert game.host_id == "host"
    assert game.map_id == "grid"

    rows = await _event_rows(sessions, "g1")
    assert len(rows) == 1
    assert rows[0].seq == 1
    assert rows[0].type == "game.created"
    assert rows[0].operation_id == "op-1"

    players = await _game_players(sessions, "g1")
    assert players == [], "the host does not join in create(); that goes through the runtime"


async def test_the_persisted_genesis_event_carries_the_map_digest(
    clean_db: None, sessions: async_sessionmaker[AsyncSession]
) -> None:
    """Asserted on the decoded `GameCreated`: `create_initial_state` does not
    copy `map_sha256` onto `GameState`, so there is no field there to check."""
    await _seed_user(sessions, "host")
    repo = GameRepository(sessions)

    await repo.create(
        game_id=GameId("g1"),
        map_id=MapId("grid"),
        rules=DEFAULT_RULES,
        host_id=PlayerId("host"),
        map_sha256=MAP_SHA,
        preset_id=None,
        operation_id="op-1",
    )

    uow = UnitOfWork(sessions)
    async with uow.begin() as tx:
        events = await tx.load_stream(GameId("g1"))

    assert len(events) == 1
    genesis = events[0]
    assert isinstance(genesis, ev.GameCreated)
    assert genesis.map_sha256 == MAP_SHA
    assert genesis.map_id == MapId("grid")
    assert genesis.host_id == PlayerId("host")
    assert genesis.rules == DEFAULT_RULES


async def test_created_game_folds_to_an_empty_lobby(
    clean_db: None, sessions: async_sessionmaker[AsyncSession]
) -> None:
    await _seed_user(sessions, "host")
    repo = GameRepository(sessions)

    await repo.create(
        game_id=GameId("g1"),
        map_id=MapId("grid"),
        rules=DEFAULT_RULES,
        host_id=PlayerId("host"),
        map_sha256=MAP_SHA,
        preset_id=None,
        operation_id="op-1",
    )

    uow = UnitOfWork(sessions)
    async with uow.begin() as tx:
        events = await tx.load_stream(GameId("g1"))
    genesis = events[0]
    assert isinstance(genesis, ev.GameCreated)

    state = create_initial_state(genesis, GameId("g1"), grid_map())
    assert state.seq == 1
    assert state.phase is Phase.LOBBY
    assert state.players == {}
    assert all(t.owner_id is None for t in state.territories.values())
    assert set(state.territories) == set(grid_map().region_ids())
    assert state.rules == DEFAULT_RULES


# --------------------------------------------------------------------------
# get_summary / list_joinable
# --------------------------------------------------------------------------


async def test_get_summary_returns_none_for_an_unknown_game(
    clean_db: None, sessions: async_sessionmaker[AsyncSession]
) -> None:
    repo = GameRepository(sessions)
    assert await repo.get_summary(GameId("nope")) is None


async def test_get_summary_reports_player_count_for_a_populated_lobby(
    clean_db: None, sessions: async_sessionmaker[AsyncSession]
) -> None:
    await _seed_user(sessions, "host")
    await _seed_user(sessions, "p1")
    await _seed_game_row(sessions, "g1", host_id="host")
    await _seed_player(sessions, "g1", "p1", 0)

    repo = GameRepository(sessions)
    summary = await repo.get_summary(GameId("g1"))

    assert summary == GameSummary(
        game_id=GameId("g1"),
        map_id=MapId("grid"),
        host_id=PlayerId("host"),
        status="lobby",
        max_players=3,
        player_count=1,
        created_at=NOW,
    )


async def test_list_joinable_hides_a_player_less_lobby(
    clean_db: None, sessions: async_sessionmaker[AsyncSession]
) -> None:
    """§6.2's crash window: GET /api/games must not advertise a lobby
    nobody can be in."""
    await _seed_user(sessions, "host")
    await _seed_game_row(sessions, "g1", host_id="host")

    repo = GameRepository(sessions)
    assert await repo.list_joinable() == ()


async def test_list_joinable_includes_a_populated_lobby(
    clean_db: None, sessions: async_sessionmaker[AsyncSession]
) -> None:
    await _seed_user(sessions, "host")
    await _seed_user(sessions, "p1")
    await _seed_game_row(sessions, "g1", host_id="host")
    await _seed_player(sessions, "g1", "p1", 0)

    repo = GameRepository(sessions)
    summaries = await repo.list_joinable()

    assert len(summaries) == 1
    assert summaries[0].game_id == GameId("g1")
    assert summaries[0].player_count == 1


# --------------------------------------------------------------------------
# find_empty_lobbies — the reaper's first policy
# --------------------------------------------------------------------------


async def test_find_empty_lobbies_finds_a_lobby_only_the_database_knows(
    clean_db: None, sessions: async_sessionmaker[AsyncSession]
) -> None:
    await _seed_user(sessions, "host")
    old = NOW - timedelta(minutes=10)
    await _seed_game_row(sessions, "g1", host_id="host", created_at=old)

    repo = GameRepository(sessions)
    ids = await repo.find_empty_lobbies(created_before=NOW - timedelta(minutes=5))

    assert ids == (GameId("g1"),)


async def test_find_empty_lobbies_ignores_a_populated_lobby(
    clean_db: None, sessions: async_sessionmaker[AsyncSession]
) -> None:
    await _seed_user(sessions, "host")
    await _seed_user(sessions, "p1")
    old = NOW - timedelta(minutes=10)
    await _seed_game_row(sessions, "g1", host_id="host", created_at=old)
    await _seed_player(sessions, "g1", "p1", 0)

    repo = GameRepository(sessions)
    ids = await repo.find_empty_lobbies(created_before=NOW - timedelta(minutes=5))

    assert ids == ()


async def test_find_empty_lobbies_ignores_a_recent_one(
    clean_db: None, sessions: async_sessionmaker[AsyncSession]
) -> None:
    await _seed_user(sessions, "host")
    recent = NOW - timedelta(minutes=1)
    await _seed_game_row(sessions, "g1", host_id="host", created_at=recent)

    repo = GameRepository(sessions)
    ids = await repo.find_empty_lobbies(created_before=NOW - timedelta(minutes=5))

    assert ids == ()


# --------------------------------------------------------------------------
# find_stale_lobbies — the reaper's second policy
# --------------------------------------------------------------------------


async def test_find_stale_lobbies_includes_a_populated_lobby(
    clean_db: None, sessions: async_sessionmaker[AsyncSession]
) -> None:
    """The reason this is not one query with find_empty_lobbies: a lobby
    that filled up and was then simply never started must still be reaped."""
    await _seed_user(sessions, "host")
    await _seed_user(sessions, "p1")
    old = NOW - timedelta(hours=7)
    await _seed_game_row(sessions, "g1", host_id="host", created_at=old)
    await _seed_player(sessions, "g1", "p1", 0)

    repo = GameRepository(sessions)
    ids = await repo.find_stale_lobbies(created_before=NOW - timedelta(hours=6))

    assert ids == (GameId("g1"),)


async def test_find_stale_lobbies_ignores_a_lobby_inside_the_cutoff(
    clean_db: None, sessions: async_sessionmaker[AsyncSession]
) -> None:
    await _seed_user(sessions, "host")
    recent = NOW - timedelta(hours=1)
    await _seed_game_row(sessions, "g1", host_id="host", created_at=recent)

    repo = GameRepository(sessions)
    ids = await repo.find_stale_lobbies(created_before=NOW - timedelta(hours=6))

    assert ids == ()


async def test_find_stale_lobbies_ignores_a_started_game(
    clean_db: None, sessions: async_sessionmaker[AsyncSession]
) -> None:
    """Status LOBBY only — EXPANSION and BATTLE are never unloaded (§5.6),
    so a stale-looking but started game must not be reaped."""
    await _seed_user(sessions, "host")
    await _seed_user(sessions, "p1")
    old = NOW - timedelta(hours=7)
    await _seed_game_row(sessions, "g1", host_id="host", status="expansion", created_at=old)
    await _seed_player(sessions, "g1", "p1", 0)

    repo = GameRepository(sessions)
    ids = await repo.find_stale_lobbies(created_before=NOW - timedelta(hours=6))

    assert ids == ()


# --------------------------------------------------------------------------
# find_unfinished — startup recovery
# --------------------------------------------------------------------------


async def test_find_unfinished_returns_expansion_and_battle_only(
    clean_db: None, sessions: async_sessionmaker[AsyncSession]
) -> None:
    await _seed_user(sessions, "host")
    await _seed_game_row(sessions, "lobby-g", host_id="host", status="lobby")
    await _seed_game_row(sessions, "expansion-g", host_id="host", status="expansion")
    await _seed_game_row(sessions, "battle-g", host_id="host", status="battle")
    await _seed_game_row(sessions, "finished-g", host_id="host", status="finished")
    await _seed_game_row(sessions, "aborted-g", host_id="host", status="aborted")

    repo = GameRepository(sessions)
    ids = set(await repo.find_unfinished())

    assert ids == {GameId("expansion-g"), GameId("battle-g")}
