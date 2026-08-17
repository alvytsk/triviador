"""Rebuild a live `GameState` from the durable log.

    create_initial_state(events[0], game_id, map_defn)
    fold(that, events[1:])

is ADR-004 read literally, with the map registry supplying the one
immutable input the log references by id rather than embeds.

This module's real job is the classification. §5.6 splits recovery
failures in two: transient ones retry with backoff and stay `Recovering`;
permanent ones go straight to `Failed` without retrying, because replay
will never succeed and retrying only hides the incident. Getting that
split wrong in either direction is expensive — a permanent failure
retried forever is an invisible outage, and a transient failure marked
`Failed` needs an operator to clear something that would have fixed
itself.
"""

import logging

from triviador.domain.game import events as ev
from triviador.domain.game.genesis import create_initial_state
from triviador.domain.game.reducer import fold
from triviador.domain.game.state import GameState
from triviador.domain.ids import GameId
from triviador.maps.registry import InvalidMapError
from triviador.runtime.errors import PermanentReplayFailure
from triviador.services.ports import EventStreamCorrupt, MapProvider, UnitOfWorkPort

logger = logging.getLogger(__name__)


class GameLoader:
    def __init__(self, uow: UnitOfWorkPort, maps: MapProvider) -> None:
        self._uow = uow
        self._maps = maps

    async def load(self, game_id: GameId) -> GameState:
        try:
            async with self._uow.begin() as tx:
                events = await tx.load_stream(game_id)
        except EventStreamCorrupt as exc:
            # A real type, declared on the port and subclassed by the
            # codec's three decode errors. Matching on class-name strings
            # would silently reclassify any renamed or newly added decode
            # error as transient, and a permanent failure retried forever
            # is an outage with no error to find.
            raise PermanentReplayFailure(
                f"game {game_id}: cannot decode its log — {type(exc).__name__}: {exc}"
            ) from exc
        # Everything else — a dropped connection, a refused socket — is
        # transient and propagates unwrapped, so the manager retries it.

        if not events:
            raise PermanentReplayFailure(f"game {game_id}: empty event stream")

        genesis = events[0]
        if not isinstance(genesis, ev.GameCreated):
            raise PermanentReplayFailure(
                f"game {game_id}: stream starts with {type(genesis).__name__}, not GameCreated"
            )

        # Before `create_initial_state`, not after: that function does not
        # carry the digest onto `GameState`, so a check afterwards would
        # have nothing left to compare (Plan 3, deliberately deferred here).
        try:
            loaded = self._maps.load_with_digest(genesis.map_id)
        except InvalidMapError as exc:
            # `InvalidMapError` only: the map file is missing, malformed,
            # or structurally invalid, and none of that improves by
            # waiting. An `OSError` from the same call — an unmounted
            # volume, a transient read failure — deliberately propagates
            # instead, because marking a game `Failed` for a disk hiccup
            # would need an operator to clear something that fixed itself
            # a second later.
            raise PermanentReplayFailure(
                f"game {game_id}: map {genesis.map_id!r} is invalid — {exc}"
            ) from exc

        if loaded.sha256 != genesis.map_sha256:
            raise PermanentReplayFailure(
                f"game {game_id}: map {genesis.map_id!r} digest is {loaded.sha256}, "
                f"the log was written against {genesis.map_sha256}"
            )

        try:
            return fold(create_initial_state(genesis, game_id, loaded.definition), events[1:])
        except Exception as exc:
            # `create_initial_state` and `fold` are pure, so this failure
            # is a function of the log and the map alone: it will
            # reproduce identically on every retry, forever. Leaving it
            # unwrapped would let a `GenesisEventNotFoldable` or a reducer
            # bug sit in the backoff loop for the life of the process,
            # looking like an outage that might clear.
            raise PermanentReplayFailure(
                f"game {game_id}: its log does not fold — {type(exc).__name__}: {exc}"
            ) from exc
