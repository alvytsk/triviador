"""`WsOrigin`: how a socket command's outcome gets back to its sender.

§8.2 is explicit that the WebSocket handler must **not** await a future, so
this origin holds none. It puts an `error` frame on the connection's own
bounded outbound queue and returns. Success needs no frame at all: it
arrives as the `game.update` every subscriber receives, and a second
per-sender acknowledgement would restate a fact the client already has.
"""

from collections.abc import Sequence

from triviador.api.errors import ApiErrorCode
from triviador.api.schemas.ws import ErrorMessage
from triviador.api.ws.hub import Connection
from triviador.domain.game.actions import RejectCode
from triviador.domain.game.events import GameEvent
from triviador.services.ports import RuntimeCode


class WsOrigin:
    """Implements `services.ports.Origin`.

    Every method is non-throwing: `Connection.send` turns a full queue into
    a 4408 close rather than raising, so a delivery failure can never reach
    the runtime's fault handling (§5.2).
    """

    def __init__(self, connection: Connection, command_id: str) -> None:
        self._connection = connection
        self._command_id = command_id

    def resolve_ok(self, events: Sequence[GameEvent]) -> None:
        return None

    def resolve_noop(self) -> None:
        # Spec 1 §11.1: an `ignore` is delivered to nobody. A stale window
        # or a duplicate is a benign race, and reporting it would invite a
        # retry that is exactly wrong.
        return None

    def resolve_rejected(self, code: RejectCode, message: str) -> None:
        self._send(code, message)

    def resolve_failed(self, code: RuntimeCode, message: str) -> None:
        # `RuntimeCode` and `ApiErrorCode` share these four values by
        # construction, and `test_envelope.py` asserts they still do.
        self._send(ApiErrorCode(code.value), message)

    def _send(self, code: ApiErrorCode | RejectCode, message: str) -> None:
        self._connection.send(ErrorMessage(command_id=self._command_id, code=code, message=message))
