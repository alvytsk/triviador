"""One authenticated, multiplexed socket per browser tab (§8.1).

Three rules this file exists to enforce, in this order:

1. **The handshake is checked before anything else** — origin, then
   session. Both refusals are close codes rather than statuses, and both
   are sent *after* `accept`: an unaccepted handshake cannot carry a code,
   and §11.1 gives the client a different reaction for each one.
2. **Every `subscribe` re-authorizes.** Socket authentication is not
   sufficient; in Spec 1 a user may subscribe only to a game they play in.
3. **The actor is the principal.** A frame cannot even mention one
   (`extra="forbid"`), and the domain command is constructed here from
   `principal.user_id`.
"""

import asyncio
import contextlib
import logging
import uuid
from decimal import Decimal
from typing import Protocol

from fastapi import APIRouter, WebSocket
from pydantic import ValidationError
from starlette.websockets import WebSocketDisconnect

from triviador.api.deps import AppDependencies, deps_of
from triviador.api.errors import ApiErrorCode
from triviador.api.middleware import origin_allowed
from triviador.api.schemas.ws import (
    CLIENT_MESSAGE_ADAPTER,
    ClientMessage,
    ErrorMessage,
    HelloMessage,
    PickRegionFrame,
    PingFrame,
    PongMessage,
    ResyncFrame,
    SelectTargetFrame,
    SubmitAnswerFrame,
    SubscribeFrame,
    SurrenderFrame,
    UnsubscribeFrame,
)
from triviador.api.ws.hub import Connection, run_sender
from triviador.api.ws.origins import WsOrigin
from triviador.db.security import token_digest
from triviador.domain.game.actions import (
    Command,
    PickRegion,
    SelectAttackTarget,
    SubmitAnswer,
    Surrender,
)
from triviador.domain.game.state import ChoiceAnswer, NumericAnswer
from triviador.domain.ids import DeadlineId, GameId, PlayerId, RegionId
from triviador.runtime.errors import (
    GameRecovering,
    GameUnrecoverable,
    RuntimeClosed,
    ServerBusy,
    ServerRestarting,
)
from triviador.runtime.runtime import GameRuntime, QueuedCommand

logger = logging.getLogger(__name__)
router = APIRouter()

_FAILURE_CODES: tuple[tuple[type[Exception], ApiErrorCode], ...] = (
    (ServerBusy, ApiErrorCode.SERVER_BUSY),
    (RuntimeClosed, ApiErrorCode.SERVER_BUSY),
    (ServerRestarting, ApiErrorCode.SERVER_RESTARTING),
    (GameRecovering, ApiErrorCode.GAME_RECOVERING),
    (GameUnrecoverable, ApiErrorCode.GAME_UNRECOVERABLE),
)

# The subset of `ClientMessage` that carries a command rather than a
# subscription action — everything `_dispatch`'s match does not handle
# itself. All four share `command_id`/`game_id` (both inherit `_Command`
# in `schemas/ws.py`), which is what lets `_command` and `_to_command`
# read those fields without a `# type: ignore[union-attr]`: every member
# of *this* union has them, unlike the full `ClientMessage` union.
CommandFrame = SubmitAnswerFrame | PickRegionFrame | SelectTargetFrame | SurrenderFrame


class WsSocket(Protocol):
    async def accept(self) -> None: ...
    async def receive_text(self) -> str: ...
    async def send_text(self, text: str) -> None: ...
    async def close(self, code: int) -> None: ...


@router.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket) -> None:
    deps = deps_of(websocket)  # reads `app.state`, exactly as a request does
    await serve_connection(
        socket=websocket,
        deps=deps,
        cookie_token=websocket.cookies.get(deps.settings.session_cookie_name),
        origin=websocket.headers.get("origin"),
    )


async def serve_connection(
    *, socket: WsSocket, deps: AppDependencies, cookie_token: str | None, origin: str | None
) -> None:
    await socket.accept()

    if not origin_allowed(origin or "", deps.settings.allowed_origins):
        await socket.close(4403)
        return

    principal = (
        None
        if not cookie_token
        else await deps.sessions.resolve(token_digest(cookie_token), now=deps.clock.now())
    )
    if principal is None:
        await socket.close(4401)
        return

    connection = Connection(
        id=uuid.uuid4().hex,
        principal=principal,
        socket=socket,
        queue_size=deps.settings.ws_outbound_queue_size,
    )
    deps.hub.add(connection)
    sender = asyncio.create_task(run_sender(connection), name=f"ws-sender:{connection.id}")
    connection.send(HelloMessage(server_time=deps.clock.now()))
    client_gone = False
    try:
        await _read_loop(connection, deps)
    except WebSocketDisconnect:
        client_gone = True
    finally:
        subscribed_games = [
            t.removeprefix("game:") for t in connection.topics if t.startswith("game:")
        ]
        deps.hub.remove(connection)
        # One scheduling turn for the sender to drain whatever is still
        # queued — a `hello` this connection never got to send, say —
        # before it either receives a close frame or is stopped outright.
        # `Connection.close`'s drain-then-sentinel behaviour is deliberate
        # for 4408 (the queue is already backed up and nothing in it will
        # ever be delivered), but a graceful exit should still land what
        # was already queued; `test_a_message_reaches_the_socket_through_
        # the_sender_task` in `test_ws_hub.py` establishes this exact
        # one-`sleep(0)` pattern.
        await asyncio.sleep(0)
        if client_gone and connection.close_code is None:
            # The client already hung up (`WebSocketDisconnect`): there is
            # no one left to read a close frame, so the sender is stopped
            # directly rather than asked to write one to a transport that
            # is already gone. `close_code` is still recorded directly
            # (bypassing `Connection.close`'s enqueue-a-sentinel path) so
            # this connection reads as closed to anything that inspects it
            # after this point, the same way every other exit leaves it.
            connection.close_code = 1000
            sender.cancel()
        else:
            connection.close(connection.close_code or 1000)
        for game_id in subscribed_games:
            # After removal, so the departing tab is already absent from the
            # roster everyone else receives.
            deps.broadcaster.presence(GameId(game_id))
        with contextlib.suppress(asyncio.CancelledError):
            await sender


async def _read_loop(connection: Connection, deps: AppDependencies) -> None:
    while connection.close_code is None:
        try:
            # §8.6's server half. A read that never returns is the normal
            # shape of a half-open socket, so the loop is bounded rather
            # than trusting the transport to notice.
            raw = await asyncio.wait_for(
                connection.socket.receive_text(), timeout=deps.settings.ws_idle_timeout_s
            )
        except TimeoutError:
            connection.close(1001)
            return
        try:
            frame = CLIENT_MESSAGE_ADAPTER.validate_json(raw)
        except ValidationError:
            _error(connection, None, ApiErrorCode.VALIDATION_FAILED, "frame failed validation")
            continue
        await _dispatch(connection, deps, frame)


async def _dispatch(connection: Connection, deps: AppDependencies, frame: ClientMessage) -> None:
    match frame:
        case PingFrame():
            connection.send(PongMessage(server_time=deps.clock.now()))
        case SubscribeFrame(topic=topic):
            await _subscribe(connection, deps, topic, resync=False)
        case ResyncFrame(topic=topic):
            await _subscribe(connection, deps, topic, resync=True)
        case UnsubscribeFrame(topic=topic):
            deps.hub.unsubscribe(connection, topic)
        case SubmitAnswerFrame() | PickRegionFrame() | SelectTargetFrame() | SurrenderFrame():
            await _command(connection, deps, frame)


async def _subscribe(
    connection: Connection, deps: AppDependencies, topic: str, *, resync: bool
) -> None:
    if topic == "lobby":
        deps.hub.subscribe(connection, topic)
        connection.send(await deps.lobby_message("lobby.snapshot"))
        return

    game_id = GameId(topic.removeprefix("game:"))
    runtime = await _runtime_or_none(connection, deps, game_id)
    if runtime is None:
        return
    if PlayerId(connection.principal.user_id) not in runtime.state.players:
        # §8.1: every subscribe authorizes for itself. The whole connection
        # closes rather than the subscription being silently dropped —
        # `4403` is a code §11.1 gives the client an explicit reaction for,
        # and there is no per-topic error channel that would carry it.
        connection.close(4403)
        return

    if not resync:
        deps.hub.subscribe(connection, topic)
    deps.broadcaster.snapshot_to(connection, game_id, runtime.state)
    if not resync:
        deps.broadcaster.presence(game_id)


async def _command(connection: Connection, deps: AppDependencies, frame: CommandFrame) -> None:
    game_id = GameId(frame.game_id)
    command_id: str = frame.command_id
    runtime = await _runtime_or_none(connection, deps, game_id, command_id=command_id)
    if runtime is None:
        return

    actor = PlayerId(connection.principal.user_id)
    if actor not in runtime.state.players:
        _error(connection, command_id, ApiErrorCode.FORBIDDEN, "not a participant in that game")
        return

    try:
        runtime.submit(
            QueuedCommand(
                command=_to_command(frame, actor),
                # Unique per (connection, command_id): the operation id is
                # the idempotency key an ambiguous commit reconciles on
                # (§5.5), so two tabs reusing the same client-side id must
                # not collide.
                operation_id=f"{connection.id}:{command_id}",
                origin=WsOrigin(connection, command_id),
            )
        )
    except Exception as exc:
        _error(connection, command_id, *_failure(exc))


def _to_command(frame: CommandFrame, actor: PlayerId) -> Command:
    """Where the actor comes from. §6.5: "the hub constructs the domain
    command with `actor_id = principal.user_id`"."""
    match frame:
        case SubmitAnswerFrame(deadline_id=deadline_id, payload=payload):
            value = (
                ChoiceAnswer(payload.idx)
                if payload.kind == "choice"
                else NumericAnswer(Decimal(payload.value))
            )
            return SubmitAnswer(actor, DeadlineId(deadline_id), value)
        case PickRegionFrame(deadline_id=deadline_id, payload=payload):
            return PickRegion(actor, DeadlineId(deadline_id), RegionId(payload.region_id))
        case SelectTargetFrame(deadline_id=deadline_id, payload=payload):
            return SelectAttackTarget(actor, DeadlineId(deadline_id), RegionId(payload.region_id))
        case SurrenderFrame():
            return Surrender(actor)
        case _:
            raise AssertionError(f"not a command frame: {frame!r}")


async def _runtime_or_none(
    connection: Connection,
    deps: AppDependencies,
    game_id: GameId,
    *,
    command_id: str | None = None,
) -> GameRuntime | None:
    try:
        return await deps.manager.get(game_id)
    except Exception as exc:
        _error(connection, command_id, *_failure(exc))
        return None


def _failure(exc: Exception) -> tuple[ApiErrorCode, str]:
    """The code *and* the message, decided together.

    Returning only the code and letting each caller pass `str(exc)` was a
    leak: the unexpected branch is reached by a loader or driver exception,
    whose text routinely carries a connection string or a fragment of SQL.
    That is precisely what §6.3 stops a 500 body from doing, and a socket
    frame is no less visible to the client than a response body.

    The five known conditions keep their message: each is one of our own
    exception types raised with a message written for a client to read.
    """
    for exc_type, code in _FAILURE_CODES:
        if isinstance(exc, exc_type):
            return code, str(exc)
    logger.exception("unexpected failure serving a socket frame")
    return ApiErrorCode.INTERNAL_ERROR, "internal error"


def _error(
    connection: Connection, command_id: str | None, code: ApiErrorCode, message: str
) -> None:
    connection.send(ErrorMessage(command_id=command_id, code=code, message=message))
