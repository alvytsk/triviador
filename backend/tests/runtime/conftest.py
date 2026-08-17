"""Builders shared across the runtime suite.

`lobby_state` and friends live in `tests/conftest.py` and are reused as-is
— the runtime tests assert on runtime behaviour, not on new state shapes.
"""

from datetime import UTC, datetime

import pytest

from tests.runtime.fakes import FakeBroadcaster, FakeClock, FakeSubscribers

T0 = datetime(2026, 8, 17, 12, 0, tzinfo=UTC)


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock(T0)


@pytest.fixture
def broadcaster() -> FakeBroadcaster:
    return FakeBroadcaster()


@pytest.fixture
def subscribers() -> FakeSubscribers:
    return FakeSubscribers()
