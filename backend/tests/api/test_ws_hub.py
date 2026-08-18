"""§8.6's outbound path: the runtime never awaits a socket write.

Everything the runtime can reach — `send`, `close`, `subscriber_count` — is
synchronous, and the only thing that touches the socket is the sender task.
"""

import asyncio
from typing import cast

from triviador.api.schemas.ws import HelloMessage, PongMessage, ServerMessage
from triviador.api.ws.hub import Connection, Hub, run_sender
from triviador.domain.ids import SessionId, UserId
from triviador.services.identity import AuthenticatedPrincipal, UserRole

T0 = __import__("tests.api.fakes", fromlist=["T0"]).T0


class FakeSocket:
    def __init__(self) -> None:
        self.sent: list[str] = []
        self.closed_with: int | None = None
        self.blocked = asyncio.Event()
        self.blocked.set()

    async def accept(self) -> None:
        pass

    async def receive_text(self) -> str:
        raise NotImplementedError

    async def send_text(self, text: str) -> None:
        await self.blocked.wait()
        self.sent.append(text)

    async def close(self, code: int) -> None:
        self.closed_with = code


def principal(user_id: str = "u1") -> AuthenticatedPrincipal:
    return AuthenticatedPrincipal(UserId(user_id), UserRole.PLAYER, SessionId(f"s-{user_id}"))


def a_connection(socket: FakeSocket | None = None, **kw: object) -> Connection:
    return Connection(
        id=str(kw.get("id", "c1")),
        principal=principal(str(kw.get("user_id", "u1"))),
        socket=socket or FakeSocket(),
        queue_size=int(kw.get("queue_size", 64)),  # type: ignore[call-overload]
    )


def queued(connection: Connection) -> list[ServerMessage]:
    """Peek the private outbound queue without consuming it — same
    rationale as `tests/runtime/conftest.py::queued_commands`: `asyncio
    .Queue` has no public peek, and reaching into `_outbound` from several
    test modules is worse than reaching into it from one. `asyncio.Queue`'s
    underlying deque is genuinely private, not merely undocumented, so
    `mypy --strict` does not know it exists; the `type: ignore` is confined
    to this one helper by the same controller ruling as `queued_commands`."""
    from triviador.api.ws.hub import _Close

    items = connection._outbound._queue  # type: ignore[attr-defined]
    return [item for item in items if not isinstance(item, _Close)]


def parsed(connection: Connection) -> list[dict[str, object]]:
    """The queued messages, as the JSON dicts a client would receive."""
    return [item.model_dump(mode="json") for item in queued(connection)]


async def test_a_message_reaches_the_socket_through_the_sender_task() -> None:
    socket = FakeSocket()
    connection = a_connection(socket)
    task = asyncio.create_task(run_sender(connection))
    connection.send(HelloMessage(server_time=T0))
    await asyncio.sleep(0)
    connection.close(1000)
    await task
    assert '"hello"' in socket.sent[0]
    assert socket.closed_with == 1000


def test_send_is_synchronous_and_returns_nothing_awaitable() -> None:
    """The contract `Broadcaster` exists to enforce: `publish` is a `def`,
    so anything it calls must be too. A coroutine here would be silently
    never awaited and the message would simply never arrive."""
    connection = a_connection()
    # `cast` only defeats mypy's "this call always returns None, why check
    # it" complaint about the assertion below; `connection.send` itself is
    # called exactly as `Connection.send` declares it, `-> None`.
    result = cast(object, connection.send(HelloMessage(server_time=T0)))
    assert result is None


async def test_a_full_outbound_queue_closes_that_subscriber_with_4408() -> None:
    """§8.6's backpressure rule and Spec 1 §12.2's scenario: a client that
    never reads must not stall the loop. It is closed; it reconnects and
    takes a snapshot (§8.5)."""
    socket = FakeSocket()
    socket.blocked.clear()  # the sender parks on the first write
    connection = a_connection(socket, queue_size=2)
    task = asyncio.create_task(run_sender(connection))
    for _ in range(5):
        connection.send(HelloMessage(server_time=T0))
    socket.blocked.set()
    await task
    assert socket.closed_with == 4408


async def test_closing_discards_whatever_was_still_queued() -> None:
    """The queue is full by definition when 4408 fires, so the close
    sentinel has to displace something. Delivering a partial backlog to a
    connection that is being closed helps nobody and the sentinel must not
    itself raise `QueueFull`."""
    socket = FakeSocket()
    socket.blocked.clear()
    connection = a_connection(socket, queue_size=1)
    task = asyncio.create_task(run_sender(connection))
    connection.send(HelloMessage(server_time=T0))
    connection.send(PongMessage(server_time=T0))
    connection.send(PongMessage(server_time=T0))
    socket.blocked.set()
    await task
    assert socket.closed_with == 4408
    assert len(socket.sent) <= 1


def test_a_second_close_is_ignored() -> None:
    """Origins, the broadcaster and the read loop can all decide to close
    the same connection; the first code wins, as it does for origins."""
    connection = a_connection()
    connection.close(4403)
    connection.close(1011)
    assert connection.close_code == 4403


def test_subscribing_indexes_the_connection_under_its_topic() -> None:
    hub, connection = Hub(), a_connection()
    hub.add(connection)
    hub.subscribe(connection, "game:g1")
    assert list(hub.subscribers("game:g1")) == [connection]
    assert hub.subscriber_count("g1") == 1


def test_unsubscribing_removes_it_and_leaves_no_empty_topic_behind() -> None:
    """An index that accumulates empty sets is a slow leak in a process
    that is meant to run for months."""
    hub, connection = Hub(), a_connection()
    hub.add(connection)
    hub.subscribe(connection, "game:g1")
    hub.unsubscribe(connection, "game:g1")
    assert hub.subscriber_count("g1") == 0
    assert "game:g1" not in hub.topics


def test_removing_a_connection_removes_every_subscription_it_held() -> None:
    hub, connection = Hub(), a_connection()
    hub.add(connection)
    hub.subscribe(connection, "game:g1")
    hub.subscribe(connection, "lobby")
    hub.remove(connection)
    assert hub.subscriber_count("g1") == 0
    assert list(hub.subscribers("lobby")) == []


def test_closing_a_games_subscribers_uses_the_code_it_was_given() -> None:
    """`GameSubscriberControl`: the manager asks, the hub acts. 1011 on
    quarantine, 1001 on shutdown (§5.6) — the manager chooses, because only
    it knows which."""
    hub = Hub()
    here, elsewhere = a_connection(id="c1"), a_connection(id="c2", user_id="u2")
    for connection in (here, elsewhere):
        hub.add(connection)
    hub.subscribe(here, "game:g1")
    hub.subscribe(elsewhere, "game:g2")
    hub.close_game_subscribers("g1", 1011)
    assert here.close_code == 1011
    assert elsewhere.close_code is None


def test_revoking_a_session_closes_exactly_that_connection_with_4401() -> None:
    """§6.5's session revocation, and Spec 1 §7's reason opaque tokens were
    chosen. Plan 7's deactivate endpoint is the caller; the mechanism is
    testable now."""
    hub = Hub()
    one, two = a_connection(id="c1", user_id="u1"), a_connection(id="c2", user_id="u2")
    for connection in (one, two):
        hub.add(connection)
    hub.close_sessions((SessionId("s-u1"),), 4401)
    assert one.close_code == 4401
    assert two.close_code is None


def test_presence_lists_the_participants_currently_connected() -> None:
    hub = Hub()
    one, two = a_connection(id="c1", user_id="u1"), a_connection(id="c2", user_id="u2")
    for connection in (one, two):
        hub.add(connection)
        hub.subscribe(connection, "game:g1")
    assert set(hub.players_in("g1")) == {"u1", "u2"}


def test_two_tabs_of_one_user_count_once_in_presence() -> None:
    """Presence is about people, not sockets: §8.1 is one socket per browser
    *tab*, and a player with two tabs open is one player in the room."""
    hub = Hub()
    for i in (1, 2):
        connection = a_connection(id=f"c{i}", user_id="u1")
        hub.add(connection)
        hub.subscribe(connection, "game:g1")
    assert hub.players_in("g1") == ("u1",)
