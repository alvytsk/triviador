"""`UnitOfWork`/`TransactionContext`: the optimistic append (§4.4) and the
read-model projection written in the same transaction (§4.2).

Two properties matter more than any individual test: the `UPDATE games ...
WHERE last_seq = :expected` check is what makes a stale append fail instead
of silently corrupting the stream, and `PlayerLeft` must delete the
`game_players` row it undoes, or Plan 2's lowest-unused-seat allocation
collides with `UNIQUE(game_id, seat)` the next time that seat is taken.
"""

import asyncio
from collections.abc import Sequence

import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from triviador.db.errors import ConcurrentModification
from triviador.db.models.auth import User
from triviador.db.models.games import Game, GameEventRow, GamePlayer
from triviador.db.unit_of_work import PersistedEventRef, UnitOfWork
from triviador.domain.game.events import (
    GameAborted,
    GameEvent,
    GameFinished,
    GameStarted,
    PlayerJoined,
    PlayerLeft,
)
from triviador.domain.ids import GameId, PlayerId

pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="session")]


# --------------------------------------------------------------------------
# fixtures: seed users/games directly, bypassing genesis (out of scope here
# — Task 7's GameRepository.create writes the games row and seq-1
# GameCreated together; append() assumes the games row already exists)
# --------------------------------------------------------------------------


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


async def _seed_game(
    sessionmaker: async_sessionmaker[AsyncSession],
    game_id: str,
    *,
    host_id: str,
    last_seq: int = 0,
) -> None:
    async with sessionmaker() as session:
        session.add(
            Game(
                id=game_id,
                map_id="m1",
                rules={},
                status="lobby",
                host_id=host_id,
                last_seq=last_seq,
            )
        )
        await session.commit()


async def _event_rows(
    sessionmaker: async_sessionmaker[AsyncSession], game_id: str
) -> Sequence[GameEventRow]:
    async with sessionmaker() as session:
        result = await session.execute(
            select(GameEventRow).where(GameEventRow.game_id == game_id).order_by(GameEventRow.seq)
        )
        return result.scalars().all()


async def _get_game(sessionmaker: async_sessionmaker[AsyncSession], game_id: str) -> Game:
    async with sessionmaker() as session:
        game = await session.get(Game, game_id)
        assert game is not None
        return game


async def _game_players(
    sessionmaker: async_sessionmaker[AsyncSession], game_id: str
) -> Sequence[GamePlayer]:
    async with sessionmaker() as session:
        result = await session.execute(
            select(GamePlayer).where(GamePlayer.game_id == game_id).order_by(GamePlayer.seat)
        )
        return result.scalars().all()


# --------------------------------------------------------------------------
# append + optimistic check
# --------------------------------------------------------------------------


async def test_append_writes_events_and_advances_last_seq(
    clean_db: None, sessions: async_sessionmaker[AsyncSession]
) -> None:
    await _seed_user(sessions, "host")
    await _seed_user(sessions, "p1")
    await _seed_game(sessions, "g1", host_id="host", last_seq=0)

    uow = UnitOfWork(sessions)
    events: tuple[GameEvent, ...] = (PlayerJoined(PlayerId("p1"), "Alice", 0),)
    async with uow.begin() as tx:
        await tx.append(GameId("g1"), expected_last_seq=0, events=events, operation_id="op-1")

    game = await _get_game(sessions, "g1")
    assert game.last_seq == 1

    rows = await _event_rows(sessions, "g1")
    assert len(rows) == 1
    assert rows[0].seq == 1
    assert rows[0].type == "game.player_joined"
    assert rows[0].operation_id == "op-1"


async def test_appended_events_read_back_identical(
    clean_db: None, sessions: async_sessionmaker[AsyncSession]
) -> None:
    """Round-trip through PostgreSQL, not just through the codec: JSONB
    normalizes key order and rejects some values the codec might emit."""
    await _seed_user(sessions, "host")
    await _seed_user(sessions, "p1")
    await _seed_user(sessions, "p2")
    await _seed_game(sessions, "g1", host_id="host", last_seq=0)

    uow = UnitOfWork(sessions)
    events: tuple[GameEvent, ...] = (
        PlayerJoined(PlayerId("p1"), "Alice", 0),
        PlayerJoined(PlayerId("p2"), "Bob", 1),
        GameStarted((PlayerId("p1"), PlayerId("p2"))),
    )
    async with uow.begin() as tx:
        await tx.append(GameId("g1"), expected_last_seq=0, events=events, operation_id="op-1")

    async with uow.begin() as tx:
        loaded = await tx.load_stream(GameId("g1"))

    assert loaded == events


async def test_stale_expected_last_seq_raises_concurrent_modification(
    clean_db: None, sessions: async_sessionmaker[AsyncSession]
) -> None:
    """Two transactions genuinely concurrent, both computing from the same
    `last_seq`. `SET LOCAL lock_timeout` bounds the second's wait on the
    `games` row lock instead of letting a stuck test hang forever; the
    outcome is asserted, never a sleep. The loser must not have written
    anything — the rollback is what makes the check meaningful."""
    await _seed_user(sessions, "host")
    await _seed_user(sessions, "p1")
    await _seed_user(sessions, "p2")
    await _seed_game(sessions, "g1", host_id="host", last_seq=5)

    uow = UnitOfWork(sessions)

    async def attempt(player_id: str, seat: int) -> None:
        async with uow.begin() as tx:
            await tx.session.execute(text("SET LOCAL lock_timeout = '2s'"))
            await tx.append(
                GameId("g1"),
                expected_last_seq=5,
                events=(PlayerJoined(PlayerId(player_id), player_id, seat),),
                operation_id=f"op-{player_id}",
            )

    results = await asyncio.gather(attempt("p1", 0), attempt("p2", 1), return_exceptions=True)

    successes = [r for r in results if r is None]
    failures = [r for r in results if isinstance(r, ConcurrentModification)]
    assert len(successes) == 1, results
    assert len(failures) == 1, results

    game = await _get_game(sessions, "g1")
    assert game.last_seq == 6

    rows = await _event_rows(sessions, "g1")
    assert len(rows) == 1, "the loser's rollback must leave zero rows behind"


async def test_concurrent_modification_leaves_no_partial_events(
    clean_db: None, sessions: async_sessionmaker[AsyncSession]
) -> None:
    """Same shape as the previous test, with a two-event batch on each side:
    whichever one loses, a rollback that only partially undid its inserts
    would still corrupt the stream, even though the `games` row would look
    consistent. Which side wins the row lock is a genuine race — the DB
    decides, not this test — so the assertions are keyed off the outcome
    rather than off which coroutine was listed first."""
    await _seed_user(sessions, "host")
    await _seed_user(sessions, "p1")
    await _seed_user(sessions, "p2")
    await _seed_user(sessions, "p3")
    await _seed_user(sessions, "p4")
    await _seed_game(sessions, "g1", host_id="host", last_seq=5)

    uow = UnitOfWork(sessions)

    async def attempt(operation_id: str, events: tuple[GameEvent, ...]) -> None:
        async with uow.begin() as tx:
            await tx.session.execute(text("SET LOCAL lock_timeout = '2s'"))
            await tx.append(
                GameId("g1"), expected_last_seq=5, events=events, operation_id=operation_id
            )

    batch_a: tuple[GameEvent, ...] = (
        PlayerJoined(PlayerId("p1"), "p1", 0),
        PlayerJoined(PlayerId("p2"), "p2", 1),
    )
    batch_b: tuple[GameEvent, ...] = (
        PlayerJoined(PlayerId("p3"), "p3", 2),
        PlayerJoined(PlayerId("p4"), "p4", 3),
    )
    results = await asyncio.gather(
        attempt("op-a", batch_a), attempt("op-b", batch_b), return_exceptions=True
    )
    successes = [r for r in results if r is None]
    failures = [r for r in results if isinstance(r, ConcurrentModification)]
    assert len(successes) == 1, results
    assert len(failures) == 1, results

    winning_batch = batch_a if results[0] is None else batch_b
    winning_op = "op-a" if results[0] is None else "op-b"

    rows = await _event_rows(sessions, "g1")
    assert len(rows) == len(winning_batch), "the loser's rollback must leave zero rows behind"
    assert all(row.operation_id == winning_op for row in rows)

    players = await _game_players(sessions, "g1")
    expected_players = {e.player_id for e in winning_batch if isinstance(e, PlayerJoined)}
    assert {p.user_id for p in players} == expected_players


async def test_append_is_rejected_when_events_is_empty(
    clean_db: None, sessions: async_sessionmaker[AsyncSession]
) -> None:
    """§5.2 resolves a no-op before reaching append; an empty append that
    silently advanced last_seq would corrupt the stream."""
    await _seed_user(sessions, "host")
    await _seed_game(sessions, "g1", host_id="host", last_seq=0)

    uow = UnitOfWork(sessions)
    with pytest.raises(ValueError):
        async with uow.begin() as tx:
            await tx.append(GameId("g1"), expected_last_seq=0, events=(), operation_id="op-1")

    game = await _get_game(sessions, "g1")
    assert game.last_seq == 0


# --------------------------------------------------------------------------
# read-model projection
# --------------------------------------------------------------------------


async def test_status_started_at_finished_at_winner_are_projected(
    clean_db: None, sessions: async_sessionmaker[AsyncSession]
) -> None:
    await _seed_user(sessions, "host")
    await _seed_user(sessions, "p1")
    await _seed_user(sessions, "p2")
    await _seed_game(sessions, "g1", host_id="host", last_seq=0)

    uow = UnitOfWork(sessions)
    async with uow.begin() as tx:
        await tx.append(
            GameId("g1"),
            expected_last_seq=0,
            events=(
                PlayerJoined(PlayerId("p1"), "Alice", 0),
                PlayerJoined(PlayerId("p2"), "Bob", 1),
                GameStarted((PlayerId("p1"), PlayerId("p2"))),
            ),
            operation_id="op-1",
        )

    game = await _get_game(sessions, "g1")
    assert game.status == "expansion"
    assert game.started_at is not None
    assert game.started_at.tzinfo is not None
    assert game.finished_at is None

    async with uow.begin() as tx:
        await tx.append(
            GameId("g1"),
            expected_last_seq=3,
            events=(GameFinished(PlayerId("p1"), {PlayerId("p1"): 100, PlayerId("p2"): 50}),),
            operation_id="op-2",
        )

    game = await _get_game(sessions, "g1")
    assert game.status == "finished"
    assert game.finished_at is not None
    assert game.finished_at.tzinfo is not None
    assert game.winner_id == "p1"


async def test_game_aborted_sets_status_and_finished_at_with_no_winner(
    clean_db: None, sessions: async_sessionmaker[AsyncSession]
) -> None:
    await _seed_user(sessions, "host")
    await _seed_game(sessions, "g1", host_id="host", last_seq=0)

    uow = UnitOfWork(sessions)
    async with uow.begin() as tx:
        await tx.append(
            GameId("g1"),
            expected_last_seq=0,
            events=(GameAborted("host left"),),
            operation_id="op-1",
        )

    game = await _get_game(sessions, "g1")
    assert game.status == "aborted"
    assert game.finished_at is not None
    assert game.winner_id is None


async def test_player_joined_inserts_a_game_player_row(
    clean_db: None, sessions: async_sessionmaker[AsyncSession]
) -> None:
    await _seed_user(sessions, "host")
    await _seed_user(sessions, "p1")
    await _seed_game(sessions, "g1", host_id="host", last_seq=0)

    uow = UnitOfWork(sessions)
    async with uow.begin() as tx:
        await tx.append(
            GameId("g1"),
            expected_last_seq=0,
            events=(PlayerJoined(PlayerId("p1"), "Alice", 3),),
            operation_id="op-1",
        )

    players = await _game_players(sessions, "g1")
    assert len(players) == 1
    assert players[0].user_id == "p1"
    assert players[0].seat == 3
    assert players[0].final_score is None


async def test_player_left_deletes_the_row_so_the_seat_can_be_reused(
    clean_db: None, sessions: async_sessionmaker[AsyncSession]
) -> None:
    """p2 leaves seat 1; p4 joins and takes seat 1. Without the DELETE this
    violates UNIQUE(game_id, seat) — the database half of Plan 2's seat fix
    (`_decide_join` allocates the lowest unused seat, so a departure must
    actually free its row here, not just logically in the domain state)."""
    await _seed_user(sessions, "host")
    for pid in ("p1", "p2", "p3", "p4"):
        await _seed_user(sessions, pid)
    await _seed_game(sessions, "g1", host_id="host", last_seq=0)

    uow = UnitOfWork(sessions)
    async with uow.begin() as tx:
        await tx.append(
            GameId("g1"),
            expected_last_seq=0,
            events=(
                PlayerJoined(PlayerId("p1"), "p1", 0),
                PlayerJoined(PlayerId("p2"), "p2", 1),
                PlayerJoined(PlayerId("p3"), "p3", 2),
            ),
            operation_id="op-1",
        )

    players = await _game_players(sessions, "g1")
    assert {p.user_id for p in players} == {"p1", "p2", "p3"}

    async with uow.begin() as tx:
        await tx.append(
            GameId("g1"),
            expected_last_seq=3,
            events=(PlayerLeft(PlayerId("p2")),),
            operation_id="op-2",
        )

    players = await _game_players(sessions, "g1")
    assert {p.user_id for p in players} == {"p1", "p3"}
    assert 1 not in {p.seat for p in players}

    # This is the assertion that fails without the DELETE: seat 1 is free
    # again, and p4 taking it must not violate UNIQUE(game_id, seat).
    async with uow.begin() as tx:
        await tx.append(
            GameId("g1"),
            expected_last_seq=4,
            events=(PlayerJoined(PlayerId("p4"), "p4", 1),),
            operation_id="op-3",
        )

    players = await _game_players(sessions, "g1")
    assert {(p.user_id, p.seat) for p in players} == {("p1", 0), ("p3", 2), ("p4", 1)}


async def test_final_scores_are_projected_on_game_finished(
    clean_db: None, sessions: async_sessionmaker[AsyncSession]
) -> None:
    await _seed_user(sessions, "host")
    await _seed_user(sessions, "p1")
    await _seed_user(sessions, "p2")
    await _seed_game(sessions, "g1", host_id="host", last_seq=0)

    uow = UnitOfWork(sessions)
    async with uow.begin() as tx:
        await tx.append(
            GameId("g1"),
            expected_last_seq=0,
            events=(
                PlayerJoined(PlayerId("p1"), "Alice", 0),
                PlayerJoined(PlayerId("p2"), "Bob", 1),
            ),
            operation_id="op-1",
        )
    async with uow.begin() as tx:
        await tx.append(
            GameId("g1"),
            expected_last_seq=2,
            events=(GameFinished(PlayerId("p1"), {PlayerId("p1"): 300, PlayerId("p2"): 150}),),
            operation_id="op-2",
        )

    players = await _game_players(sessions, "g1")
    scores = {p.user_id: p.final_score for p in players}
    assert scores == {"p1": 300, "p2": 150}


# --------------------------------------------------------------------------
# events_for_operation (§5.5 reconciliation)
# --------------------------------------------------------------------------


async def test_events_for_operation_returns_seqs_and_ordered_types(
    clean_db: None, sessions: async_sessionmaker[AsyncSession]
) -> None:
    await _seed_user(sessions, "host")
    await _seed_user(sessions, "p1")
    await _seed_user(sessions, "p2")
    await _seed_game(sessions, "g1", host_id="host", last_seq=0)

    uow = UnitOfWork(sessions)
    async with uow.begin() as tx:
        await tx.append(
            GameId("g1"),
            expected_last_seq=0,
            events=(
                PlayerJoined(PlayerId("p1"), "Alice", 0),
                PlayerJoined(PlayerId("p2"), "Bob", 1),
                GameStarted((PlayerId("p1"), PlayerId("p2"))),
            ),
            operation_id="op-1",
        )

    async with uow.begin() as tx:
        refs = await tx.events_for_operation(GameId("g1"), "op-1")

    assert refs == (
        PersistedEventRef(seq=1, type="game.player_joined"),
        PersistedEventRef(seq=2, type="game.player_joined"),
        PersistedEventRef(seq=3, type="game.started"),
    )


async def test_events_for_operation_distinguishes_a_same_length_different_batch(
    clean_db: None, sessions: async_sessionmaker[AsyncSession]
) -> None:
    """Two operations of equal length on one game: the refs for each must
    carry that operation's own types, in order. This is the test that fails
    if `events_for_operation` returns bare seqs."""
    await _seed_user(sessions, "host")
    await _seed_user(sessions, "p1")
    await _seed_user(sessions, "p2")
    await _seed_game(sessions, "g1", host_id="host", last_seq=0)

    uow = UnitOfWork(sessions)
    async with uow.begin() as tx:
        await tx.append(
            GameId("g1"),
            expected_last_seq=0,
            events=(
                PlayerJoined(PlayerId("p1"), "Alice", 0),
                PlayerJoined(PlayerId("p2"), "Bob", 1),
            ),
            operation_id="op-a",
        )
    async with uow.begin() as tx:
        await tx.append(
            GameId("g1"),
            expected_last_seq=2,
            events=(
                PlayerLeft(PlayerId("p1")),
                GameAborted("host left"),
            ),
            operation_id="op-b",
        )

    async with uow.begin() as tx:
        refs_a = await tx.events_for_operation(GameId("g1"), "op-a")
        refs_b = await tx.events_for_operation(GameId("g1"), "op-b")

    assert refs_a == (
        PersistedEventRef(seq=1, type="game.player_joined"),
        PersistedEventRef(seq=2, type="game.player_joined"),
    )
    assert refs_b == (
        PersistedEventRef(seq=3, type="game.player_left"),
        PersistedEventRef(seq=4, type="game.aborted"),
    )


async def test_events_for_operation_is_empty_for_an_uncommitted_operation(
    clean_db: None, sessions: async_sessionmaker[AsyncSession]
) -> None:
    """The ambiguous-commit case where the transaction did not land. Empty
    is the signal to re-run, not an error."""
    await _seed_user(sessions, "host")
    await _seed_game(sessions, "g1", host_id="host", last_seq=0)

    uow = UnitOfWork(sessions)
    async with uow.begin() as tx:
        refs = await tx.events_for_operation(GameId("g1"), "never-ran")

    assert refs == ()
