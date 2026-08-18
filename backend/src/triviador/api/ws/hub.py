"""Connections, topics, and the one task allowed to touch a socket.

The shape follows §8.6 exactly:

    runtime ──put_nowait──► bounded outbound queue (~64) ──► sender ──► socket
                                  │ QueueFull
                                  ▼
                            close(4408)

Everything reachable from the runtime — `Connection.send`, `close`, and the
hub's `close_game_subscribers` / `subscriber_count` — is a `def`. That is
not a stylistic choice: `Broadcaster.publish` is synchronous precisely so
that awaiting a socket write from the consumer loop cannot compile.
"""

import logging
from asyncio import Queue, QueueEmpty, QueueFull
from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field
from typing import Protocol

from triviador.api.schemas.ws import ServerMessage, game_topic
from triviador.domain.ids import SessionId
from triviador.services.identity import AuthenticatedPrincipal

logger = logging.getLogger(__name__)


class Socket(Protocol):
    """What a WebSocket connection can be asked to do.

    Wider than the hub itself needs — `Hub` and `Connection` only ever
    call `send_text` and `close` — but `accept` and `receive_text` are
    declared here too (controller ruling R-2) because Task 16's endpoint
    needs them, and a narrower Protocol would force that seam through a
    `# type: ignore`. Starlette's `WebSocket` satisfies this structurally,
    and so does a four-method test double.
    """

    async def accept(self) -> None: ...
    async def receive_text(self) -> str: ...
    async def send_text(self, text: str) -> None: ...
    async def close(self, code: int) -> None: ...


@dataclass(frozen=True)
class _Close:
    code: int


@dataclass
class Connection:
    id: str
    principal: AuthenticatedPrincipal
    socket: Socket
    queue_size: int = 64
    topics: set[str] = field(default_factory=set)
    close_code: int | None = None
    _outbound: "Queue[ServerMessage | _Close]" = field(init=False)

    def __post_init__(self) -> None:
        self._outbound = Queue(maxsize=self.queue_size)

    def send(self, message: ServerMessage) -> None:
        """Never blocks, never raises, never awaits."""
        if self.close_code is not None:
            return
        try:
            self._outbound.put_nowait(message)
        except QueueFull:
            # §8.6: a client that is not reading is closed, not waited for.
            # It reconnects and takes a snapshot, which is cheaper and more
            # correct than an unbounded buffer.
            logger.info("connection %s: outbound queue full, closing 4408", self.id)
            self.close(4408)

    def close(self, code: int) -> None:
        """First code wins, like an origin's first outcome.

        The queue is full by definition in the 4408 case, so it is drained
        before the sentinel is queued — otherwise the close itself would
        raise `QueueFull` and the connection would stay open forever with
        nobody reading it.
        """
        if self.close_code is not None:
            return
        self.close_code = code
        while True:
            try:
                self._outbound.get_nowait()
            except QueueEmpty:
                break
        self._outbound.put_nowait(_Close(code))

    async def next_outbound(self) -> "ServerMessage | _Close":
        return await self._outbound.get()


async def run_sender(connection: Connection) -> None:
    """The only thing that touches the socket (§8.6).

    Exits on the close sentinel, and on any send failure — a socket that
    raised is a socket that is gone, and continuing to drain into it is a
    task that never ends.
    """
    while True:
        item = await connection.next_outbound()
        if isinstance(item, _Close):
            try:
                await connection.socket.close(item.code)
            except Exception:  # a dead socket is the normal case here
                logger.debug("connection %s: close failed on a dead socket", connection.id)
            return
        try:
            await connection.socket.send_text(item.model_dump_json())
        except Exception:
            logger.info("connection %s: send failed, ending sender", connection.id)
            connection.close_code = connection.close_code or 1011
            return


class Hub:
    """Connections by id, and the topic index over them."""

    def __init__(self) -> None:
        self.connections: dict[str, Connection] = {}
        self.topics: dict[str, set[str]] = {}

    def add(self, connection: Connection) -> None:
        self.connections[connection.id] = connection

    def remove(self, connection: Connection) -> None:
        self.connections.pop(connection.id, None)
        for topic in list(connection.topics):
            self.unsubscribe(connection, topic)

    def subscribe(self, connection: Connection, topic: str) -> None:
        connection.topics.add(topic)
        self.topics.setdefault(topic, set()).add(connection.id)

    def unsubscribe(self, connection: Connection, topic: str) -> None:
        connection.topics.discard(topic)
        holders = self.topics.get(topic)
        if holders is None:
            return
        holders.discard(connection.id)
        if not holders:
            # Empty sets are a slow leak in a process meant to run for
            # months: one entry per game ever played.
            del self.topics[topic]

    def subscribers(self, topic: str) -> Iterator[Connection]:
        for connection_id in tuple(self.topics.get(topic, ())):
            connection = self.connections.get(connection_id)
            if connection is not None and connection.close_code is None:
                yield connection

    # --- GameSubscriberControl ---------------------------------------------

    def close_game_subscribers(self, game_id: str, code: int) -> None:
        for connection in tuple(self.subscribers(game_topic(game_id))):
            connection.close(code)

    def subscriber_count(self, game_id: str) -> int:
        return sum(1 for _ in self.subscribers(game_topic(game_id)))

    # --- identity-driven closes --------------------------------------------

    def close_sessions(self, session_ids: Iterable[SessionId], code: int) -> None:
        """§6.5: session revocation closes with `4401`."""
        wanted = set(session_ids)
        for connection in tuple(self.connections.values()):
            if connection.principal.session_id in wanted:
                connection.close(code)

    def players_in(self, game_id: str) -> tuple[str, ...]:
        """Presence is per person, not per socket (§8.1: one socket per
        browser *tab*)."""
        seen: dict[str, None] = {}
        for connection in self.subscribers(game_topic(game_id)):
            seen.setdefault(str(connection.principal.user_id), None)
        return tuple(seen)
