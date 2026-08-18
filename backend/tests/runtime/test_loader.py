"""Replay, and the line between "try again later" and "this will never
work". §5.6: transient faults stay `Recovering` with backoff; permanent
ones go straight to `Failed` without retrying, because retrying only hides
the incident."""

from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager

import pytest

from tests.conftest import grid_map
from triviador.domain.game.events import GameCreated, GameEvent, PlayerJoined
from triviador.domain.game.rules import DEFAULT_RULES
from triviador.domain.game.state import Phase
from triviador.domain.ids import GameId, MapId, PlayerId
from triviador.maps.registry import InvalidMapError, LoadedMap
from triviador.runtime.errors import PermanentReplayFailure
from triviador.runtime.loader import GameLoader
from triviador.services.ports import EventRef, QuestionBankPort, ReconcileOutcome

GOOD_DIGEST = "a" * 64
GAME = GameId("g1")


class StubMaps:
    def __init__(self, digest: str = GOOD_DIGEST, raises: Exception | None = None) -> None:
        self._digest = digest
        self._raises = raises

    def available(self) -> tuple[MapId, ...]:
        return (MapId("grid"),)

    def load_with_digest(self, map_id: MapId) -> LoadedMap:
        if self._raises is not None:
            raise self._raises
        return LoadedMap(definition=grid_map(), sha256=self._digest)


class StubUnitOfWork:
    """Only `load_stream` is exercised here — the loader never appends,
    looks up an operation, or reconciles one. Those methods, and
    `questions`, are still part of the Protocol this class must satisfy
    (`services.ports.Transaction`, yielded by `begin` as itself), so they
    raise rather than being silently absent — omitting them would let
    `GameLoader.load` narrow its parameter type away from the full port
    without a test noticing."""

    def __init__(self, events: Sequence[GameEvent] = (), raises: Exception | None = None) -> None:
        self._events = tuple(events)
        self._raises = raises

    @asynccontextmanager
    async def begin(self) -> AsyncIterator["StubUnitOfWork"]:
        yield self

    @property
    def questions(self) -> QuestionBankPort:
        raise AssertionError("not reached in loader tests")

    async def append(
        self,
        game_id: GameId,
        *,
        expected_last_seq: int,
        events: Sequence[GameEvent],
        operation_id: str,
    ) -> None:
        raise AssertionError("not reached in loader tests")

    async def load_stream(self, game_id: GameId) -> tuple[GameEvent, ...]:
        if self._raises is not None:
            raise self._raises
        return self._events

    async def events_for_operation(
        self, game_id: GameId, operation_id: str
    ) -> tuple[EventRef, ...]:
        raise AssertionError("not reached in loader tests")

    async def operation_matches(
        self,
        game_id: GameId,
        operation_id: str,
        *,
        expected_base_seq: int,
        events: Sequence[GameEvent],
    ) -> ReconcileOutcome:
        raise AssertionError("not reached in loader tests")


def genesis(digest: str = GOOD_DIGEST) -> GameCreated:
    return GameCreated(
        map_id=MapId("grid"),
        rules=DEFAULT_RULES,
        host_id=PlayerId("p1"),
        map_sha256=digest,
    )


async def test_loads_a_lobby_from_its_genesis_event() -> None:
    loader = GameLoader(uow=StubUnitOfWork([genesis()]), maps=StubMaps())

    state = await loader.load(GAME)

    assert state.phase is Phase.LOBBY
    assert state.seq == 1
    assert state.map.map_id == MapId("grid")


async def test_folds_every_event_after_genesis() -> None:
    loader = GameLoader(
        uow=StubUnitOfWork([genesis(), PlayerJoined(PlayerId("p1"), "P1", seat=0)]), maps=StubMaps()
    )

    state = await loader.load(GAME)

    assert state.seq == 2
    assert PlayerId("p1") in state.players


async def test_a_digest_mismatch_is_permanent() -> None:
    """The map file changed under a live game. Every region id in the log
    may now name a different region, so the log can never be replayed
    into a state that means what it meant when it was written."""
    loader = GameLoader(uow=StubUnitOfWork([genesis("b" * 64)]), maps=StubMaps(GOOD_DIGEST))

    with pytest.raises(PermanentReplayFailure):
        await loader.load(GAME)


async def test_an_invalid_map_is_permanent() -> None:
    loader = GameLoader(
        uow=StubUnitOfWork([genesis()]), maps=StubMaps(raises=InvalidMapError("no map.json"))
    )

    with pytest.raises(PermanentReplayFailure):
        await loader.load(GAME)


async def test_a_transient_map_read_failure_is_not_permanent() -> None:
    """An unmounted volume is not a corrupt map. Wrapping this would mark
    the game `Failed` over a disk hiccup, and `Failed` is cleared only by
    operator action — so a fault that fixed itself in a second would need
    a human to notice it."""
    loader = GameLoader(
        uow=StubUnitOfWork([genesis()]), maps=StubMaps(raises=OSError("input/output error"))
    )

    with pytest.raises(OSError):
        await loader.load(GAME)


async def test_a_log_that_does_not_fold_is_permanent() -> None:
    """`fold` is pure, so this failure is a function of the log and the
    map alone and will reproduce identically forever. Left unwrapped it
    would sit in the recovery backoff loop for the life of the process,
    looking like an outage that might clear.

    Build a stream whose second event is a second `GameCreated` —
    `evolve` raises `GenesisEventNotFoldable` on it.
    """
    loader = GameLoader(uow=StubUnitOfWork([genesis(), genesis()]), maps=StubMaps())

    with pytest.raises(PermanentReplayFailure):
        await loader.load(GAME)


async def test_an_empty_stream_is_permanent() -> None:
    """No genesis event means no `games` row worth loading — replaying an
    empty log will never produce a state, however long we wait."""
    loader = GameLoader(uow=StubUnitOfWork([]), maps=StubMaps())

    with pytest.raises(PermanentReplayFailure):
        await loader.load(GAME)


async def test_a_stream_not_starting_with_genesis_is_permanent() -> None:
    loader = GameLoader(
        uow=StubUnitOfWork([PlayerJoined(PlayerId("p1"), "P1", seat=0)]), maps=StubMaps()
    )

    with pytest.raises(PermanentReplayFailure):
        await loader.load(GAME)


async def test_a_decode_failure_is_permanent() -> None:
    """An unknown wire type with no upcaster. §5.6 names this exactly:
    permanent, no retry."""
    from triviador.db.errors import UnknownEventType

    loader = GameLoader(
        uow=StubUnitOfWork(raises=UnknownEventType("battle.unheard_of")), maps=StubMaps()
    )

    with pytest.raises(PermanentReplayFailure):
        await loader.load(GAME)


async def test_a_database_failure_propagates_unchanged() -> None:
    """Transient. It must *not* be wrapped, because wrapping it would send
    the registry entry to `Failed` and stop the retries that would have
    fixed it once the database came back."""
    loader = GameLoader(uow=StubUnitOfWork(raises=OSError("connection refused")), maps=StubMaps())

    with pytest.raises(OSError):
        await loader.load(GAME)
