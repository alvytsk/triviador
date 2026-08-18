"""The fake clock is load-bearing for every later task, so it gets tested
like production code. If `advance_to` can wake a sleeper early or leave a
due one asleep, a dozen downstream tests become quietly meaningless."""

import asyncio
from datetime import UTC, datetime, timedelta

from tests.runtime.fakes import FakeClock
from triviador.runtime.clock import SystemClock

T0 = datetime(2026, 8, 17, 12, 0, tzinfo=UTC)


def test_system_clock_returns_aware_utc() -> None:
    """A naive datetime here survives until a process restart compares an
    absolute deadline across it, which is exactly the bug ADR-001 exists
    to prevent."""
    now = SystemClock().now()
    assert now.tzinfo is UTC


async def test_system_clock_sleep_until_a_past_instant_returns_immediately() -> None:
    """Recovery calls this with deadlines that already expired. It must
    not sleep for a negative duration, and it must not raise."""
    clock = SystemClock()
    await clock.sleep_until(clock.now() - timedelta(hours=1))


async def test_fake_clock_does_not_move_on_its_own() -> None:
    clock = FakeClock(T0)
    await asyncio.sleep(0)
    assert clock.now() == T0


async def test_fake_clock_wakes_only_the_sleepers_that_are_due() -> None:
    clock = FakeClock(T0)
    woken: list[str] = []

    async def sleeper(name: str, at: datetime) -> None:
        await clock.sleep_until(at)
        woken.append(name)

    early = asyncio.create_task(sleeper("early", T0 + timedelta(seconds=10)))
    late = asyncio.create_task(sleeper("late", T0 + timedelta(seconds=30)))
    await clock.settle()
    assert clock.pending() == (T0 + timedelta(seconds=10), T0 + timedelta(seconds=30))

    await clock.advance_to(T0 + timedelta(seconds=10))
    assert woken == ["early"]
    assert clock.pending() == (T0 + timedelta(seconds=30),)

    await clock.advance_to(T0 + timedelta(seconds=30))
    assert woken == ["early", "late"]
    await asyncio.gather(early, late)


async def test_fake_clock_sleep_until_the_past_returns_without_registering() -> None:
    clock = FakeClock(T0)
    await clock.sleep_until(T0 - timedelta(seconds=1))
    assert clock.pending() == ()


async def test_fake_clock_advance_to_never_moves_backwards() -> None:
    """A test that rewinds time is a test asserting something that cannot
    happen in production. Fail loudly instead of silently."""
    clock = FakeClock(T0)
    try:
        await clock.advance_to(T0 - timedelta(seconds=1))
    except ValueError:
        return
    raise AssertionError("advance_to must reject a backwards jump")
