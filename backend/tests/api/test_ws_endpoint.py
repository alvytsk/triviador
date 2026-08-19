"""§6.5 and §8.1: what a socket may do, and as whom."""

import json
from typing import Any, NoReturn

import pytest
from starlette.websockets import WebSocketDisconnect

from tests.api.conftest import ORIGIN, replace_deps
from tests.conftest import lobby_state
from tests.runtime.conftest import manager_with_resident, queued_commands, stalled_runtime
from tests.runtime.fakes import T0, RecordingOrigin
from tests.runtime.fakes import FakeClock as RuntimeFakeClock
from triviador.api.deps import AppDependencies
from triviador.api.ws.endpoint import WsSocket, serve_connection
from triviador.domain.game.actions import Surrender
from triviador.domain.ids import DeadlineId, GameId, PlayerId, SessionId
from triviador.runtime.manager import Live, Recovering
from triviador.runtime.runtime import QueuedCommand


class ScriptedSocket:
    """A fixed script of client frames, then a disconnect."""

    def __init__(self, *frames: object) -> None:
        self._frames = [f if isinstance(f, str) else json.dumps(f) for f in frames]
        self.sent: list[dict[str, Any]] = []
        self.accepted = False
        self.closed_with: int | None = None
        self._disconnected = False

    async def accept(self) -> None:
        self.accepted = True

    async def receive_text(self) -> str:
        if not self._frames:
            self._disconnected = True
            raise WebSocketDisconnect(1000)
        return self._frames.pop(0)

    async def send_text(self, text: str) -> None:
        self.sent.append(json.loads(text))

    async def close(self, code: int) -> None:
        if self._disconnected:
            # Matches real Starlette/uvicorn: writing a close frame to a
            # transport the client already hung up on raises (there,
            # `OSError` surfacing as `WebSocketDisconnect(1006)`) —
            # `run_sender`'s `except Exception` around this call already
            # treats that as the ordinary case of a dead socket.
            raise OSError("socket is already disconnected")
        self.closed_with = code

    def types(self) -> list[str]:
        return [m["type"] for m in self.sent]


async def serve(
    deps: AppDependencies,
    socket: WsSocket,
    *,
    token: str | None = "tok",
    origin: str | None = ORIGIN,
) -> None:
    await serve_connection(socket=socket, deps=deps, cookie_token=token, origin=origin)


def foreign_game(deps: AppDependencies) -> AppDependencies:
    """The same manager wiring, holding a game `u1` is not in."""
    manager, _ = manager_with_resident(
        lobby_state({"someone": 0}), RuntimeFakeClock(T0), start=False
    )
    return replace_deps(deps, manager=manager)


async def test_a_foreign_origin_is_refused_with_4403(deps: AppDependencies) -> None:
    """§6.4. The socket is accepted first and then closed: a handshake
    refused before `accept` cannot carry a close code, and §11.1 gives the
    client a distinct reaction per code — which it can only read if it
    arrives."""
    socket = ScriptedSocket()
    await serve(deps, socket, origin="http://evil.lan")
    assert socket.accepted and socket.closed_with == 4403
    assert socket.sent == []


async def test_a_missing_or_dead_session_is_refused_with_4401(deps: AppDependencies) -> None:
    socket = ScriptedSocket()
    await serve(deps, socket, token=None)
    assert socket.closed_with == 4401

    revoked = ScriptedSocket()
    await deps.sessions.revoke(SessionId("s1"), at=deps.clock.now())
    await serve(deps, revoked, token="tok")
    assert revoked.closed_with == 4401


async def test_an_authenticated_socket_is_greeted_with_the_server_time(
    deps: AppDependencies,
) -> None:
    """§8.6: `hello` carries `server_time`; the client refines the offset
    from ping/pong afterwards, because a snapshot timestamp would embed
    one-way network delay."""
    socket = ScriptedSocket()
    await serve(deps, socket)
    assert socket.types() == ["hello"]
    assert socket.sent[0]["server_time"].startswith("2026-")


async def test_ping_is_answered_with_pong(deps: AppDependencies) -> None:
    socket = ScriptedSocket({"type": "ping"})
    await serve(deps, socket)
    assert socket.types() == ["hello", "pong"]


async def test_subscribing_to_a_game_yields_a_snapshot_and_presence(deps: AppDependencies) -> None:
    socket = ScriptedSocket({"type": "subscribe", "topic": "game:g1"})
    await serve(deps, socket)
    assert socket.types() == ["hello", "game.snapshot", "game.presence"]
    assert socket.sent[1]["state"]["you"]["player_id"] == "u1"


async def test_subscribing_to_someone_elses_game_closes_with_4403(deps: AppDependencies) -> None:
    """§8.1: "Every `subscribe` performs its own authorization. Socket-level
    authentication is not sufficient." In Spec 1 that means participation."""
    socket = ScriptedSocket({"type": "subscribe", "topic": "game:g1"})
    await serve(foreign_game(deps), socket)
    assert socket.closed_with == 4403


async def test_subscribing_to_an_unknown_game_id_yields_not_found_without_polluting_the_manager(
    deps: AppDependencies,
) -> None:
    """`_runtime_or_none`'s `games.get_summary` guard, mirroring the REST
    routes' (`api/http/games.py`). Without it, an id with no rows would
    reach `manager.get` → `_load` → a failed replay → a permanently parked
    `Failed` registry entry (plus a lock) for an id nothing ever created —
    unbounded, since only operator action clears `Failed` (§5.6), and
    surfaced through `manager.degraded()` into `/api/health/ready`. The
    second assertion is the one that would have caught that: not just the
    right error code, but no registry entry left behind."""
    socket = ScriptedSocket({"type": "subscribe", "topic": "game:ghost"})
    await serve(deps, socket)
    assert socket.sent[1]["code"] == "not_found"
    assert GameId("ghost") not in deps.manager._entries


async def test_resync_re_sends_the_snapshot_without_re_announcing_presence(
    deps: AppDependencies,
) -> None:
    """§8.5: a reconnect renders from scratch. Presence has not changed, so
    re-broadcasting it would flicker every other client's roster."""
    socket = ScriptedSocket(
        {"type": "subscribe", "topic": "game:g1"}, {"type": "resync", "topic": "game:g1"}
    )
    await serve(deps, socket)
    assert socket.types().count("game.snapshot") == 2
    assert socket.types().count("game.presence") == 1


async def test_unsubscribing_stops_the_connection_counting_as_a_subscriber(
    deps: AppDependencies,
) -> None:
    socket = ScriptedSocket(
        {"type": "subscribe", "topic": "game:g1"}, {"type": "unsubscribe", "topic": "game:g1"}
    )
    await serve(deps, socket)
    assert deps.hub.subscriber_count("g1") == 0


async def test_unsubscribing_rebroadcasts_presence_without_the_departed_player(
    deps: AppDependencies,
) -> None:
    """§8.3: "Presence changes broadcast `game.presence`." The disconnect
    path already re-broadcasts on departure; explicit `unsubscribe` — a tab
    navigating back to the lobby without closing the socket — must do the
    same, or the departed player lingers in every other subscriber's
    roster indefinitely.

    Driven directly through `_dispatch` (rather than two concurrent
    `serve_connection` scripts) so the ordering between the two
    connections' frames is not left to the event loop's scheduling."""
    from tests.api.test_ws_hub import FakeSocket, a_connection, parsed
    from triviador.api.schemas.ws import UnsubscribeFrame
    from triviador.api.ws.endpoint import _dispatch

    stayer = a_connection(FakeSocket(), id="stayer", user_id="u1")
    leaver = a_connection(FakeSocket(), id="leaver", user_id="u2")
    deps.hub.add(stayer)
    deps.hub.add(leaver)
    deps.hub.subscribe(stayer, "game:g1")
    deps.hub.subscribe(leaver, "game:g1")

    await _dispatch(leaver, deps, UnsubscribeFrame(topic="game:g1"))

    presence = [m for m in parsed(stayer) if m["type"] == "game.presence"]
    assert presence, "unsubscribe did not rebroadcast presence"
    assert presence[-1]["connected"] == ["u1"]


@pytest.mark.parametrize(
    "frame",
    ["{not json", {"type": "nonsense"}, {"type": "subscribe", "topic": "admin:games"}],
    ids=["not-json", "unknown-type", "spec-2-topic"],
)
async def test_a_malformed_frame_is_an_error_frame_not_a_close(
    deps: AppDependencies, frame: str | dict[str, Any]
) -> None:
    """A parse failure is the client's bug, not a reason to drop a socket
    carrying a live game — the player would lose their open window over a
    typo in one frame."""
    socket = ScriptedSocket(frame)
    await serve(deps, socket)
    assert socket.types() == ["hello", "error"]
    assert socket.sent[1]["code"] == "validation_failed"
    assert socket.closed_with is None


async def test_a_frame_carrying_an_actor_is_refused_as_validation(deps: AppDependencies) -> None:
    """The first of §11's two separate properties: the field is
    unacceptable, and strictness rejects it before any actor is derived."""
    socket = ScriptedSocket(
        {"type": "surrender", "command_id": "c1", "game_id": "g1", "actor_id": "u2"}
    )
    await serve(deps, socket)
    assert socket.sent[1]["code"] == "validation_failed"


async def test_a_command_is_built_with_the_sessions_identity(deps: AppDependencies) -> None:
    """The second property. Asserted against the command that actually
    reached the queue — a successful command has no response to inspect."""
    runtime = deps.manager.live_runtimes()[0]
    socket = ScriptedSocket({"type": "surrender", "command_id": "c1", "game_id": "g1"})
    await serve(deps, socket)
    (queued,) = [q for q in queued_commands(runtime) if not q.stop]
    assert isinstance(queued.command, Surrender)
    assert queued.command.actor_id == PlayerId("u1")


async def test_an_answer_frame_becomes_the_domain_command(deps: AppDependencies) -> None:
    from decimal import Decimal

    from triviador.domain.game.actions import SubmitAnswer
    from triviador.domain.game.state import NumericAnswer

    runtime = deps.manager.live_runtimes()[0]
    socket = ScriptedSocket(
        {
            "type": "submit_answer",
            "command_id": "c1",
            "game_id": "g1",
            "deadline_id": 3,
            "payload": {"kind": "numeric", "value": "42.5"},
        }
    )
    await serve(deps, socket)
    (queued,) = [q for q in queued_commands(runtime) if not q.stop]
    assert queued.command == SubmitAnswer(
        PlayerId("u1"), DeadlineId(3), NumericAnswer(Decimal("42.5"))
    )


async def test_a_command_for_a_game_the_sender_is_not_in_is_refused(deps: AppDependencies) -> None:
    """Membership is re-checked per command, never inherited from having
    subscribed to something once."""
    socket = ScriptedSocket({"type": "surrender", "command_id": "c1", "game_id": "g1"})
    await serve(foreign_game(deps), socket)
    assert socket.sent[1]["code"] == "forbidden"
    assert socket.sent[1]["command_id"] == "c1"


async def test_an_unexpected_failure_never_echoes_its_exception_text(deps: AppDependencies) -> None:
    """§6.3's sanitization rule, on the socket. The exception a broken
    loader raises carries a connection string; the frame the client sees
    must not."""

    class ExplodingManager:
        async def get(self, game_id: GameId) -> NoReturn:
            raise RuntimeError("connect to postgres://user:hunter2@db failed")

    socket = ScriptedSocket({"type": "surrender", "command_id": "c1", "game_id": "g1"})
    await serve(replace_deps(deps, manager=ExplodingManager()), socket)
    assert socket.sent[1]["code"] == "internal_error"
    assert socket.sent[1]["message"] == "internal error"
    assert "hunter2" not in json.dumps(socket.sent)


async def test_a_recovering_game_answers_without_closing(deps: AppDependencies) -> None:
    """`_runtime_or_none`'s own translation, reached when `deps.manager.get`
    raises before a runtime is ever returned — a quarantine, in production.

    Marking the resident runtime `closed` does not exercise this: `_usable`
    treats a closed `Live` entry as unusable and transparently reloads a
    fresh runtime through the manager's `CountingLoader` (`GameManager`
    §5.6) — one that does not know about `u1`, so the command would be
    refused as `forbidden` before `get` ever raises. Parking the registry
    entry in `Recovering` instead raises `GameRecovering` straight out of
    `get()`, the same way a real quarantine would.
    """
    runtime = deps.manager.live_runtimes()[0]
    deps.manager._entries[runtime.game_id] = Recovering(attempt=1, next_at=deps.clock.now())
    socket = ScriptedSocket({"type": "surrender", "command_id": "c1", "game_id": "g1"})
    await serve(deps, socket)
    assert socket.sent[1]["code"] == "game_recovering"
    assert socket.closed_with is None


async def test_a_full_runtime_queue_answers_with_server_busy(deps: AppDependencies) -> None:
    """`_command`'s *own* `try: runtime.submit(...) except Exception:` —
    distinct from `_runtime_or_none`'s translation above. There is no
    `await` between `_runtime_or_none` returning a live runtime and
    `submit` being called, so in production this block is reached only by
    a genuinely full queue — an everyday load condition for this endpoint,
    not a manager-registry state.

    `stalled_runtime` is unstarted (`start=False`'s rationale throughout
    this suite), so the one command submitted directly here to fill its
    `queue_maxsize=1` queue is never drained — `runtime.submit` for the
    frame the socket sends is the second `put_nowait`, and it is the one
    that overflows.
    """
    runtime = stalled_runtime(
        lobby_state({"u1": 0, "u2": 1}), RuntimeFakeClock(T0), queue_maxsize=1
    )
    runtime.submit(
        QueuedCommand(
            command=Surrender(PlayerId("u1")), operation_id="filler", origin=RecordingOrigin()
        )
    )
    deps.manager._entries[runtime.game_id] = Live(runtime)
    socket = ScriptedSocket({"type": "surrender", "command_id": "c1", "game_id": "g1"})
    await serve(deps, socket)
    assert socket.sent[1]["code"] == "server_busy"
    assert socket.closed_with is None


async def test_a_silent_socket_is_closed_rather_than_held_forever(deps: AppDependencies) -> None:
    """§8.6's other half. The client pings every 15 s; a socket that has
    said nothing for 30 s is gone, and TCP will not tell us — a closed
    laptop lid or a Wi-Fi handover leaves the connection half-open, and
    with it a sender task and a name in every roster."""
    import asyncio

    class SilentSocket(ScriptedSocket):
        async def receive_text(self) -> str:
            await asyncio.sleep(3600)  # never speaks; the timeout must fire
            raise AssertionError("unreachable")

    socket = SilentSocket()
    # `Settings` is a Pydantic model, not a dataclass — `model_copy`, not
    # `dataclasses.replace`.
    quick = replace_deps(
        deps, settings=deps.settings.model_copy(update={"ws_idle_timeout_s": 0.01})
    )
    await asyncio.wait_for(serve(quick, socket), timeout=2)
    assert socket.closed_with == 1001


async def test_a_disconnect_removes_the_connection_and_its_subscriptions(
    deps: AppDependencies,
) -> None:
    socket = ScriptedSocket({"type": "subscribe", "topic": "game:g1"})
    await serve(deps, socket)
    assert deps.hub.connections == {}
    assert deps.hub.subscriber_count("g1") == 0


async def test_a_second_tab_of_the_same_user_is_its_own_connection(deps: AppDependencies) -> None:
    """§8.1 is one socket per browser tab, and §8.6's presence is per
    person — two connections, one name in the roster."""
    first = ScriptedSocket({"type": "subscribe", "topic": "game:g1"}, {"type": "ping"})
    second = ScriptedSocket({"type": "subscribe", "topic": "game:g1"})
    import asyncio

    await asyncio.gather(serve(deps, first), serve(deps, second))
    presence = [m for m in first.sent + second.sent if m["type"] == "game.presence"]
    assert all(m["connected"] == ["u1"] for m in presence)
