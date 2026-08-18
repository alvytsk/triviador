"""§5.5's last row: broadcaster failure never quarantines.

"The commit is durable and memory is correct; destroying a healthy runtime
over a misbehaving socket converts a client problem into a game-wide
outage." So `publish` catches everything, and the two failure modes get
two different close codes.
"""

from dataclasses import replace
from decimal import Decimal
from typing import cast

from tests.api.test_ws_hub import FakeSocket, a_connection, parsed
from tests.conftest import full_pool, lobby_state
from triviador.api.ws.broadcaster import WsBroadcaster
from triviador.api.ws.hub import Connection, Hub
from triviador.api.ws.origins import WsOrigin
from triviador.domain.game import events as ev
from triviador.domain.game.actions import RejectCode
from triviador.domain.game.state import GameState, NumericAnswer, SubmittedAnswer
from triviador.domain.ids import GameId, PlayerId
from triviador.services.ports import Broadcaster, GameSubscriberControl, RuntimeCode

# `mypy --strict` is what actually proves the two ports are satisfied —
# without these the first proof would be Task 17's `GameManager(...)` call.
_broadcaster: Broadcaster = WsBroadcaster(Hub(), media_base="/media")
_subscribers: GameSubscriberControl = WsBroadcaster(Hub(), media_base="/media")


class Boom:
    """Anything the projection touches explodes. Stands in for the whole
    class of "a bug in projection" without needing to author one."""

    def __getattr__(self, name: str) -> object:
        raise RuntimeError("projection exploded")


def hub_with(*user_ids: str) -> tuple[Hub, list[Connection]]:
    hub = Hub()
    connections: list[Connection] = []
    for i, user_id in enumerate(user_ids):
        connection = a_connection(FakeSocket(), id=f"c{i}", user_id=user_id)
        hub.add(connection)
        hub.subscribe(connection, "game:g1")
        connections.append(connection)
    return hub, connections


def playing_state() -> GameState:
    return replace(lobby_state({"p1": 0, "p2": 1}), seq=8, pool=full_pool())


def test_publish_is_synchronous_and_returns_none() -> None:
    """`Broadcaster.publish` is a `def` so the consumer loop cannot await a
    socket write by accident (§8.6). A coroutine here would typecheck
    against nothing and simply never run."""
    hub, _ = hub_with("p1")
    # `cast` only defeats mypy's "this call always returns None, why check
    # it" complaint about the assertion below — same idiom as
    # `test_ws_hub.test_send_is_synchronous_and_returns_nothing_awaitable`.
    result = cast(
        object,
        WsBroadcaster(hub, media_base="/media").publish(GameId("g1"), 7, playing_state(), ()),
    )
    assert result is None


def test_each_subscriber_gets_the_state_projected_for_them() -> None:
    """The reason `publish` takes domain objects: one commit, N different
    payloads, decided here because only the hub knows the viewers."""
    hub, (one, two) = hub_with("p1", "p2")
    event = ev.AnswerSubmitted(PlayerId("p1"), SubmittedAnswer(NumericAnswer(Decimal(99)), 900))
    WsBroadcaster(hub, media_base="/media").publish(GameId("g1"), 7, playing_state(), (event,))
    assert "99" in str(parsed(one)[0]["events"])
    assert "99" not in str(parsed(two)[0]["events"])


def test_the_update_carries_the_batch_boundaries() -> None:
    """§8.4: the client applies when `base_seq == last_seq`, ignores when
    `seq <= last_seq`, and resyncs otherwise. Both numbers are needed for
    that to be decidable at all."""
    hub, (one,) = hub_with("p1")
    WsBroadcaster(hub, media_base="/media").publish(GameId("g1"), 7, playing_state(), ())
    payload = parsed(one)[0]
    assert (payload["type"], payload["base_seq"], payload["seq"]) == ("game.update", 7, 8)


def test_events_that_project_to_none_simply_do_not_appear() -> None:
    hub, (one,) = hub_with("p1")
    WsBroadcaster(hub, media_base="/media").publish(
        GameId("g1"), 7, playing_state(), (ev.QuestionPoolDrawn(full_pool()),)
    )
    assert parsed(one)[0]["events"] == []


def test_a_subscriber_whose_projection_fails_is_closed_with_1011() -> None:
    """§5.5's second table. The connection dies; the game does not."""
    hub, (one,) = hub_with("p1")
    WsBroadcaster(hub, media_base="/media").publish(GameId("g1"), 7, Boom(), ())  # type: ignore[arg-type]
    assert one.close_code == 1011


def test_publish_never_raises_however_badly_projection_fails() -> None:
    """The property the runtime depends on: an exception out of `publish`
    reaches `_apply`'s fault handling and quarantines a game whose state is
    durable and correct."""
    hub, _ = hub_with("p1", "p2")
    WsBroadcaster(hub, media_base="/media").publish(GameId("g1"), 7, Boom(), ())  # type: ignore[arg-type]


def test_one_broken_subscriber_does_not_cost_the_others_their_update() -> None:
    """Per-connection `try`, not one around the loop: a single failure that
    aborted the whole publish would silently stall every other player, and
    §8.4's sequencing would then make them all resync."""
    hub, (bad, good) = hub_with("p1", "p2")
    broadcaster = WsBroadcaster(hub, media_base="/media")

    original = broadcaster._update

    def explode_for_bad(connection, *args, **kwargs):  # type: ignore[no-untyped-def]
        if connection is bad:
            raise RuntimeError("only this one")
        return original(connection, *args, **kwargs)

    broadcaster._update = explode_for_bad  # type: ignore[method-assign]
    broadcaster.publish(GameId("g1"), 7, playing_state(), ())
    assert bad.close_code == 1011
    assert good.close_code is None
    assert len(parsed(good)) == 1


def test_a_slow_subscriber_is_closed_with_4408_and_the_game_survives() -> None:
    hub = Hub()
    slow = a_connection(FakeSocket(), id="c0", user_id="p1", queue_size=1)
    hub.add(slow)
    hub.subscribe(slow, "game:g1")
    broadcaster = WsBroadcaster(hub, media_base="/media")
    for _ in range(5):
        broadcaster.publish(GameId("g1"), 7, playing_state(), ())
    assert slow.close_code == 4408


def test_the_broadcaster_answers_the_two_subscriber_control_questions() -> None:
    hub, (one,) = hub_with("p1")
    broadcaster = WsBroadcaster(hub, media_base="/media")
    assert broadcaster.subscriber_count(GameId("g1")) == 1
    broadcaster.close_game_subscribers(GameId("g1"), 1001)
    assert one.close_code == 1001
    assert broadcaster.subscriber_count(GameId("g1")) == 0


def test_a_snapshot_is_sent_to_one_connection_only() -> None:
    hub, (one, two) = hub_with("p1", "p2")
    WsBroadcaster(hub, media_base="/media").snapshot_to(one, GameId("g1"), playing_state())
    assert parsed(one)[0]["type"] == "game.snapshot"
    assert parsed(two) == []


def test_a_rejected_command_comes_back_as_an_error_frame_not_a_future() -> None:
    """§8.2: the WS handler does not await a future — an unobserved
    `asyncio.Future` either logs "exception was never retrieved" or
    silently swallows the rejection."""
    connection = a_connection(FakeSocket())
    WsOrigin(connection, "cmd-1").resolve_rejected(RejectCode.NOT_ADJACENT, "'r7' is not adjacent")
    assert parsed(connection)[0] == {
        "type": "error",
        "command_id": "cmd-1",
        "code": "not_adjacent",
        "message": "'r7' is not adjacent",
    }


def test_a_transport_failure_comes_back_with_its_runtime_code() -> None:
    connection = a_connection(FakeSocket())
    WsOrigin(connection, "cmd-1").resolve_failed(RuntimeCode.GAME_RECOVERING, "recovering")
    assert parsed(connection)[0]["code"] == "game_recovering"


def test_success_and_ignore_send_nothing() -> None:
    """Success reaches the client as the broadcast every subscriber gets;
    an ignore is a benign race delivered to nobody (Spec 1 §11.1)."""
    connection = a_connection(FakeSocket())
    origin = WsOrigin(connection, "cmd-1")
    origin.resolve_ok(())
    origin.resolve_noop()
    assert parsed(connection) == []


def test_an_origin_on_a_closed_connection_does_not_raise() -> None:
    """Every `Origin` method is non-throwing and idempotent (`ports.py`): a
    delivery failure on a dead socket must never reach fault handling."""
    connection = a_connection(FakeSocket())
    connection.close(4408)
    WsOrigin(connection, "cmd-1").resolve_rejected(RejectCode.GAME_FULL, "full")


def test_the_origin_satisfies_the_port() -> None:
    from triviador.services.ports import Origin

    origin: Origin = WsOrigin(a_connection(FakeSocket()), "cmd-1")
    assert origin is not None
