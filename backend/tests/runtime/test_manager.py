"""§5.6's registry. Load-once, three states, and generations that never
mix."""

import asyncio

import pytest

from tests.runtime.conftest import T0, CountingLoader, a_manager
from triviador.domain.ids import GameId
from triviador.runtime.errors import GameRecovering, GameUnrecoverable, PermanentReplayFailure
from triviador.runtime.manager import Failed, Recovering

GAME = GameId("g1")


async def test_get_loads_once_and_caches() -> None:
    loader = CountingLoader()
    manager = a_manager(loader)

    first = await manager.get(GAME)
    second = await manager.get(GAME)

    assert first is second
    assert loader.calls == 1


async def test_concurrent_gets_build_one_runtime() -> None:
    """Without the per-game lock this is ADR-002's divergence failure
    in-process: two runtimes for one game, both appending at the same
    expected_last_seq."""
    loader = CountingLoader()
    manager = a_manager(loader)

    runtimes = await asyncio.gather(*(manager.get(GAME) for _ in range(8)))

    assert len({id(r) for r in runtimes}) == 1
    assert loader.calls == 1


async def test_each_load_takes_the_next_generation() -> None:
    manager = a_manager(CountingLoader())

    first = await manager.get(GAME)
    second = await manager.get(GameId("g2"))

    assert second.generation > first.generation


async def test_a_recovering_entry_refuses_callers() -> None:
    manager = a_manager(CountingLoader())
    manager._entries[GAME] = Recovering(attempt=2, next_at=T0)

    with pytest.raises(GameRecovering):
        await manager.get(GAME)


async def test_a_failed_entry_refuses_callers_and_is_operator_visible() -> None:
    manager = a_manager(CountingLoader())
    manager._entries[GAME] = Failed(reason="map digest mismatch")

    with pytest.raises(GameUnrecoverable):
        await manager.get(GAME)
    assert manager.degraded() == ((GAME, "map digest mismatch"),)


async def test_a_permanent_load_failure_goes_straight_to_failed() -> None:
    """No backoff, no retry: replay will never succeed, and retrying only
    hides the incident (§5.6)."""
    manager = a_manager(CountingLoader(raises=PermanentReplayFailure("bad digest")))

    with pytest.raises(GameUnrecoverable):
        await manager.get(GAME)
    assert isinstance(manager.entry_for(GAME), Failed)


async def test_a_transient_load_failure_leaves_no_entry_behind() -> None:
    """A database blip on a first `get` is not a quarantine — there is no
    runtime to tear down and nothing to recover. The caller sees the
    error and the next `get` tries again from scratch."""
    manager = a_manager(CountingLoader(raises=OSError("connection refused")))

    with pytest.raises(OSError):
        await manager.get(GAME)
    assert manager.entry_for(GAME) is None


async def test_live_runtimes_lists_only_live_entries() -> None:
    manager = a_manager(CountingLoader())
    await manager.get(GAME)
    manager._entries[GameId("g2")] = Recovering(attempt=1, next_at=T0)

    assert [r.game_id for r in manager.live_runtimes()] == [GAME]


async def test_get_reloads_a_closed_runtime() -> None:
    """§5.6's generation fencing from the caller's side: a runtime that
    was closed out from under a caller is not handed out again."""
    loader = CountingLoader()
    manager = a_manager(loader)
    first = await manager.get(GAME)
    await first.aclose()

    second = await manager.get(GAME)

    assert second is not first
    assert loader.calls == 2
