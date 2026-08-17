"""§5.2: "Every origin resolves exactly once" and "origin resolution is
non-throwing and idempotent".

The second property is not defensive programming. A REST client can
disconnect while its command sits in the queue, leaving a cancelled future
whose `set_result` raises `InvalidStateError` — *after* the batch has
already committed. If that propagated, a delivery failure on a dead HTTP
request would quarantine a game whose state is durable and correct.
"""

import asyncio
from collections.abc import Callable

import pytest

from triviador.domain.game.actions import RejectCode
from triviador.domain.game.events import PlayerJoined
from triviador.domain.ids import PlayerId
from triviador.runtime.origins import (
    Accepted,
    CommandOutcome,
    Failed,
    FutureOrigin,
    Ignored,
    Rejected,
    SystemOrigin,
)
from triviador.services.ports import RuntimeCode

EVENT = PlayerJoined(PlayerId("p1"), "P1", seat=0)


async def test_future_origin_delivers_the_committed_events() -> None:
    origin = FutureOrigin()
    origin.resolve_ok([EVENT])
    assert await origin.future == Accepted((EVENT,))


async def test_future_origin_delivers_each_outcome_kind() -> None:
    cases: tuple[tuple[Callable[[FutureOrigin], None], CommandOutcome], ...] = (
        (lambda o: o.resolve_noop(), Ignored()),
        (
            lambda o: o.resolve_rejected(RejectCode.GAME_FULL, "lobby is full"),
            Rejected(RejectCode.GAME_FULL, "lobby is full"),
        ),
        (
            lambda o: o.resolve_failed(RuntimeCode.SERVER_BUSY, "queue full"),
            Failed(RuntimeCode.SERVER_BUSY, "queue full"),
        ),
    )
    for resolve, expected in cases:
        origin = FutureOrigin()
        resolve(origin)
        assert await origin.future == expected


async def test_a_second_resolution_is_a_no_op() -> None:
    """Not merely harmless — the first outcome must survive. A runtime bug
    that resolves twice would otherwise silently overwrite a success with
    a shutdown code."""
    origin = FutureOrigin()
    origin.resolve_ok([EVENT])
    origin.resolve_failed(RuntimeCode.SERVER_RESTARTING, "shutting down")

    assert await origin.future == Accepted((EVENT,))


async def test_resolving_a_cancelled_future_does_not_raise() -> None:
    """The regression test §5.2 names: cancel a REST request after its
    command is enqueued, and the command must still commit with the
    runtime healthy. Here that reduces to: this call does not raise."""
    origin = FutureOrigin()
    origin.future.cancel()
    await asyncio.sleep(0)

    origin.resolve_ok([EVENT])  # must not raise


async def test_resolving_from_another_loop_iteration_does_not_raise() -> None:
    """The future may already be done for reasons the runtime never sees.
    Every `resolve_*` swallows and logs its own failure."""
    origin = FutureOrigin()
    origin.future.set_exception(RuntimeError("set out of band"))

    origin.resolve_noop()  # must not raise
    with pytest.raises(RuntimeError):
        await origin.future


def test_system_origin_accepts_every_resolution_silently() -> None:
    """Watchdog, reaper and deadline expiries have nobody waiting. They
    still resolve, because the loop resolves unconditionally and a
    `None` origin would mean a branch on every path."""
    origin = SystemOrigin("watchdog")

    origin.resolve_ok([EVENT])
    origin.resolve_noop()
    origin.resolve_rejected(RejectCode.WRONG_TURN_STATE, "stale")
    origin.resolve_failed(RuntimeCode.GAME_RECOVERING, "quarantined")
