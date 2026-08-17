"""Lobby to a terminal phase, through the real thing. If this passes, the
fakes were telling the truth."""

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tests.runtime.fakes import RecordingOrigin
from tests.runtime.integration.conftest import (
    deactivate_all_questions,
    drain_runtime,
    event_row_count,
    event_seqs,
    fresh_manager,
    game_status,
    last_seq,
    player_seats,
    submit_and_settle,
)
from triviador.domain.game.actions import AbortGame, JoinGame, RejectCode, StartGame
from triviador.domain.game.state import Phase
from triviador.domain.ids import GameId, PlayerId
from triviador.runtime.manager import GameManager, Live
from triviador.runtime.runtime import GameRuntime, QueuedCommand

pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="session")]


async def join_all(runtime: GameRuntime) -> None:
    for pid in ("p1", "p2", "p3"):
        command = JoinGame(PlayerId(pid), pid.upper())
        origin = await submit_and_settle(runtime, command, f"join-{pid}")
        assert origin.outcome[0] == "ok"


async def test_three_joins_and_a_start_reach_expansion(
    manager: GameManager, lobby: GameId, sessions: async_sessionmaker[AsyncSession]
) -> None:
    """The path every game takes. Asserts against the *database*, not just
    memory: `last_seq` must equal the row count, or the read model and the
    log have diverged."""
    runtime = await manager.get(lobby)
    await join_all(runtime)

    origin = await submit_and_settle(runtime, StartGame(PlayerId("p1")), "start-1")

    assert origin.outcome[0] == "ok"
    assert runtime.state.phase is Phase.EXPANSION
    assert await game_status(sessions, lobby) == "expansion"
    assert await event_row_count(sessions, lobby) == runtime.state.seq
    assert await last_seq(sessions, lobby) == runtime.state.seq


async def test_the_read_model_matches_the_folded_state(
    manager: GameManager, lobby: GameId, sessions: async_sessionmaker[AsyncSession]
) -> None:
    """§4.2's projection, checked end to end: reload with a fresh loader
    and assert the rebuilt state agrees with the `games` / `game_players`
    rows on phase, seq, and seat assignment."""
    runtime = await manager.get(lobby)
    await join_all(runtime)
    await submit_and_settle(runtime, StartGame(PlayerId("p1")), "start-1")

    rebuilt = await fresh_manager(manager)._loader.load(lobby)

    assert rebuilt.phase is runtime.state.phase
    assert rebuilt.seq == runtime.state.seq
    assert await player_seats(sessions, lobby) == {
        pid: player.seat for pid, player in rebuilt.players.items()
    }


async def test_bases_are_mutually_non_adjacent_in_the_committed_log(
    manager: GameManager, lobby: GameId
) -> None:
    """Spec 1 §3.4, asserted where it finally becomes durable. The
    materialiser chose these regions and nothing downstream checks them —
    `_decide_start` validates distinctness and membership only."""
    runtime = await manager.get(lobby)
    await join_all(runtime)
    await submit_and_settle(runtime, StartGame(PlayerId("p1")), "start-1")

    bases = {player.base_region for player in runtime.state.players.values()}
    assert len(bases) == 3
    assert None not in bases
    for region in bases:
        assert region is not None
        assert runtime.state.map.neighbours(region).isdisjoint(bases)


async def test_a_start_with_a_drained_bank_is_rejected_and_the_game_stays_in_lobby(
    manager: GameManager, lobby: GameId, sessions: async_sessionmaker[AsyncSession]
) -> None:
    """§10.6's authoritative checkpoint — genuinely authoritative, because
    the `FOR SHARE` locks are still held when the events would be
    inserted. The rejection must leave no trace at all."""
    runtime = await manager.get(lobby)
    await join_all(runtime)
    seq_before = runtime.state.seq
    await deactivate_all_questions(sessions)

    origin = await submit_and_settle(runtime, StartGame(PlayerId("p1")), "start-1")

    assert origin.outcome == ("rejected", RejectCode.QUESTION_POOL_INSUFFICIENT)
    assert runtime.state.phase is Phase.LOBBY
    assert runtime.state.seq == seq_before
    assert await game_status(sessions, lobby) == "lobby"
    assert await event_row_count(sessions, lobby) == seq_before
    assert isinstance(manager.entry_for(lobby), Live)  # a rejection is not a fault


async def test_concurrent_commands_produce_a_contiguous_seq(
    manager: GameManager, lobby: GameId, sessions: async_sessionmaker[AsyncSession]
) -> None:
    """§12.2's serialization case: N commands from M origins at once →
    seq contiguous, `UNIQUE(game_id, seq)` intact, every origin resolved
    exactly once. The consumer serializes them; this proves the database
    agrees, and that no origin was dropped on the way."""
    runtime = await manager.get(lobby)
    origins = [RecordingOrigin() for _ in range(3)]
    for pid, origin in zip(("p1", "p2", "p3"), origins, strict=True):
        runtime.submit(QueuedCommand(JoinGame(PlayerId(pid), pid.upper()), f"join-{pid}", origin))
    await drain_runtime(runtime)

    assert all(len(o.resolutions) == 1 for o in origins)
    seqs = await event_seqs(sessions, lobby)
    assert seqs == list(range(1, len(seqs) + 1))
    assert await last_seq(sessions, lobby) == seqs[-1]


async def test_an_abort_reaches_a_terminal_phase_and_the_read_model(
    manager: GameManager, lobby: GameId, sessions: async_sessionmaker[AsyncSession]
) -> None:
    """The short path to a terminal status. A full play-through to
    FINISHED would be worth having, but §11.6 treats ABORTED identically
    and this pins the projection either way."""
    runtime = await manager.get(lobby)
    await submit_and_settle(runtime, JoinGame(PlayerId("p1"), "P1"), "join-p1")

    origin = await submit_and_settle(runtime, AbortGame(actor_id=None), "abort-1")

    assert origin.outcome[0] == "ok"
    assert runtime.state.phase is Phase.ABORTED
    assert await game_status(sessions, lobby) == "aborted"
