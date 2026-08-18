"""§5.6 and §12.2's recovery cases, against a real log."""

from datetime import timedelta
from pathlib import Path

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tests.runtime.fakes import FakeClock
from tests.runtime.integration.conftest import (
    drain_runtime,
    fresh_manager,
    rewrite_every_question_prompt,
    rewrite_map_adding_a_region,
    submit_and_settle,
)
from triviador.domain.game.actions import JoinGame, StartGame
from triviador.domain.ids import GameId, PlayerId
from triviador.runtime.errors import GameUnrecoverable
from triviador.runtime.manager import Failed, GameManager
from triviador.runtime.runtime import GameRuntime

pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="session")]


async def started_game(manager: GameManager, lobby: GameId) -> GameRuntime:
    runtime = await manager.get(lobby)
    for pid in ("p1", "p2", "p3"):
        await submit_and_settle(runtime, JoinGame(PlayerId(pid), pid.upper()), f"join-{pid}")
    await submit_and_settle(runtime, StartGame(PlayerId("p1")), "start-1")
    return runtime


async def test_a_restart_rebuilds_state_identical_to_the_live_one(
    manager: GameManager, lobby: GameId
) -> None:
    """`GameState` is a frozen dataclass all the way down, so `==` is the
    whole assertion.

    If it fails on a field you did not expect — `next_deadline_id` is the
    likely one — that is a finding about recovery, not a reason to loosen
    this to a field-by-field subset.
    """
    runtime = await started_game(manager, lobby)
    snapshot = runtime.state
    await manager.shutdown()

    revived = await fresh_manager(manager).get(lobby)

    assert revived.state == snapshot


async def test_a_deadline_still_in_the_future_is_scheduled_at_its_original_instant(
    manager: GameManager, lobby: GameId, clock: FakeClock
) -> None:
    """§12.2: deadline +20 s, kill the runtime, restart before it → the
    timer fires at the original absolute time, not the full window again
    from the restart."""
    runtime = await started_game(manager, lobby)
    deadline = runtime.state.current_deadline()
    assert deadline is not None
    await manager.shutdown()

    await clock.advance_to(deadline.deadline_at - timedelta(seconds=2))
    revived_manager = fresh_manager(manager)
    await revived_manager.recover_active_games()
    await clock.settle()

    assert clock.pending() == (deadline.deadline_at,)


async def test_a_deadline_already_passed_is_expired_immediately(
    manager: GameManager, lobby: GameId, clock: FakeClock
) -> None:
    """Restart after the window closed → `ExpireDeadline` is enqueued at
    once. Recovery must never extend a window a player has already
    spent."""
    runtime = await started_game(manager, lobby)
    deadline = runtime.state.current_deadline()
    assert deadline is not None
    await manager.shutdown()

    await clock.advance_to(deadline.deadline_at + timedelta(seconds=5))
    revived_manager = fresh_manager(manager)
    await revived_manager.recover_active_games()
    revived = await revived_manager.get(lobby)
    await drain_runtime(revived)

    current = revived.state.current_deadline()
    assert current is not None
    assert current.id != deadline.id  # the expiry fired and the game advanced


async def test_the_question_pool_survives_a_rewrite_of_the_questions_table(
    manager: GameManager, lobby: GameId, sessions: async_sessionmaker[AsyncSession]
) -> None:
    """§12.2's pool immutability. The pool lives in the committed
    `QuestionPoolDrawn` event, not in the table — rewriting every row must
    not change a single presented question."""
    runtime = await started_game(manager, lobby)
    pool_before = runtime.state.pool
    await manager.shutdown()

    await rewrite_every_question_prompt(sessions, "TAMPERED")

    revived = await fresh_manager(manager).get(lobby)

    assert revived.state.pool == pool_before
    assert all("TAMPERED" not in q.prompt for q in revived.state.pool.numeric)
    assert all("TAMPERED" not in q.prompt for q in revived.state.pool.multiple_choice)


async def test_a_map_digest_mismatch_makes_the_game_unrecoverable(
    manager: GameManager, lobby: GameId, map_root: Path
) -> None:
    """Rewrite `map.json` under a live game. Every region id in the log may
    now name a different region, so replay must refuse outright — not fold
    against different adjacency and carry on looking healthy."""
    runtime = await manager.get(lobby)
    await submit_and_settle(runtime, JoinGame(PlayerId("p1"), "P1"), "join-p1")
    await manager.shutdown()

    rewrite_map_adding_a_region(map_root / "grid")

    revived_manager = fresh_manager(manager)
    with pytest.raises(GameUnrecoverable):
        await revived_manager.get(lobby)

    assert isinstance(revived_manager.entry_for(lobby), Failed)
    assert [game_id for game_id, _ in revived_manager.degraded()] == [lobby]


async def test_startup_recovery_skips_lobbies_and_loads_active_games(
    manager: GameManager, lobby: GameId
) -> None:
    """`find_unfinished` is `status IN ('expansion', 'battle')`. A lobby
    holds no deadline, so loading it at boot would be work with no owner
    and no timer — and the reaper reaches the abandoned ones through the
    database anyway."""
    await started_game(manager, lobby)
    await manager.shutdown()

    revived_manager = fresh_manager(manager)
    unloadable = await revived_manager.recover_active_games()

    assert unloadable == ()
    assert [rt.game_id for rt in revived_manager.live_runtimes()] == [lobby]
