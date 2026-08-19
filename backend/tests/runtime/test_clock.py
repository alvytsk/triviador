"""The fake clock is load-bearing for every later task, so it gets tested
like production code. If `advance_to` can wake a sleeper early or leave a
due one asleep, a dozen downstream tests become quietly meaningless."""

import asyncio
from datetime import UTC, datetime, timedelta

import pytest

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


async def test_system_clock_sleep_until_never_returns_early() -> None:
    """R-24, documentation-level: repeatedly race a short real sleep and
    check the outcome from outside. This is **not** the regression test —
    the reviewer reproduced the pre-fix single-`asyncio.sleep` formula
    standalone and ran this exact external-check pattern against it
    34,000 times without a single failure, because real wall/monotonic
    clock drift is too rare to hit by chance in a test process. Kept
    anyway because it documents the intended behaviour under real
    scheduling; `test_system_clock_sleep_until_survives_a_monotonic_wall_clock_disagreement`
    below is the one that actually discriminates the fix from the bug."""
    clock = SystemClock()
    for _ in range(200):
        when = clock.now() + timedelta(milliseconds=1)
        await clock.sleep_until(when)
        assert clock.now() >= when


async def test_system_clock_sleep_until_survives_a_monotonic_wall_clock_disagreement(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """R-24's actual regression coverage. The bug was a *clock
    disagreement*: `asyncio.sleep` schedules on the loop's monotonic
    clock while `sleep_until`'s delay is computed from the wall clock, so
    a sleep can resolve before wall time has genuinely reached `when`.
    Rather than hope to observe that disagreement by luck (the test
    above does, and doesn't), inject it directly: a wall clock that
    advances by *less* than the delay `asyncio.sleep` is asked to wait
    for on its first call, standing in for a monotonic wake landing
    early. `sleep_until`'s loop must sleep again rather than trust that
    first wake; a single-sleep formula cannot, and returns early instead
    — proved by deliberately reverting to it (see the task report).
    """
    calls = 0

    class DriftingClock(SystemClock):
        wall: datetime = T0

        def now(self) -> datetime:
            return self.wall

    clock = DriftingClock()

    async def fake_sleep(delay: float) -> None:
        nonlocal calls
        calls += 1
        # The first "wake" lands early relative to wall time: only half
        # of the requested delay actually elapses on the wall clock.
        # Every later call is honest, so a correct loop still terminates.
        advance = delay / 2 if calls == 1 else delay
        clock.wall = clock.wall + timedelta(seconds=advance)

    monkeypatch.setattr(asyncio, "sleep", fake_sleep)

    when = clock.wall + timedelta(seconds=1)
    await clock.sleep_until(when)

    assert clock.now() >= when
    assert calls >= 2, "a single sleep cannot have reached `when` after an early wake"


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
