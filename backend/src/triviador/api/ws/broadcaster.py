"""The two ports the runtime holds, implemented over the hub.

`publish` is where §8.7's per-viewer projection happens, and it is the one
function in this codebase that must never raise: `GameRuntime._publish`
calls it after a durable commit, and an exception escaping it would
quarantine a game whose state is correct (§5.5).
"""

import logging
from collections.abc import Sequence

from triviador.api.projection.events import project_event
from triviador.api.projection.snapshot import project_snapshot
from triviador.api.projection.viewer import viewer_for
from triviador.api.schemas.ws import (
    PresenceMessage,
    SnapshotMessage,
    UpdateMessage,
    game_topic,
)
from triviador.api.ws.hub import Connection, Hub
from triviador.domain.game.events import GameEvent
from triviador.domain.game.state import GameState
from triviador.domain.ids import GameId

logger = logging.getLogger(__name__)


class WsBroadcaster:
    """Implements `Broadcaster` and `GameSubscriberControl`."""

    def __init__(self, hub: Hub, *, media_base: str) -> None:
        self._hub = hub
        self._media_base = media_base

    def publish(
        self,
        game_id: GameId,
        base_seq: int,
        state: GameState,
        events: Sequence[GameEvent],
    ) -> None:
        for connection in tuple(self._hub.subscribers(game_topic(str(game_id)))):
            try:
                connection.send(self._update(connection, game_id, base_seq, state, events))
            except Exception:
                # §5.5: a projection or serialization failure closes *that*
                # subscriber with 1011. The `try` is inside the loop so one
                # broken connection cannot cost the others their update, and
                # nothing propagates — the caller is the consumer task, and
                # an exception here would read as a runtime fault.
                logger.exception("projection failed for connection %s", connection.id)
                connection.close(1011)

    def _update(
        self,
        connection: Connection,
        game_id: GameId,
        base_seq: int,
        state: GameState,
        events: Sequence[GameEvent],
    ) -> UpdateMessage:
        viewer = viewer_for(state, connection.principal)
        snapshot = project_snapshot(state, viewer, media_base=self._media_base)
        projected = tuple(
            client_event
            for client_event in (project_event(event, viewer) for event in events)
            if client_event is not None
        )
        return UpdateMessage(
            game_id=str(game_id),
            base_seq=base_seq,
            seq=state.seq,
            state=snapshot.state,
            events=projected,
        )

    def snapshot_to(self, connection: Connection, game_id: GameId, state: GameState) -> None:
        """§8.5's reconnect path and §8.1's subscribe: one full snapshot,
        never an event catch-up."""
        viewer = viewer_for(state, connection.principal)
        snapshot = project_snapshot(state, viewer, media_base=self._media_base)
        connection.send(
            SnapshotMessage(game_id=str(game_id), seq=snapshot.seq, state=snapshot.state)
        )

    def presence(self, game_id: GameId) -> None:
        """§8.3: deliberately not a domain event — no `seq`, not persisted,
        absent from replay."""
        message = PresenceMessage(
            game_id=str(game_id), connected=self._hub.players_in(str(game_id))
        )
        for connection in tuple(self._hub.subscribers(game_topic(str(game_id)))):
            connection.send(message)

    # --- GameSubscriberControl ---------------------------------------------

    def close_game_subscribers(self, game_id: GameId, code: int) -> None:
        self._hub.close_game_subscribers(str(game_id), code)

    def subscriber_count(self, game_id: GameId) -> int:
        return self._hub.subscriber_count(str(game_id))
