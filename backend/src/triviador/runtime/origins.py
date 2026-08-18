"""Who is waiting for a command's outcome, and how they are told.

Two implementations cover everything: `FutureOrigin` for a caller that
awaits a result (REST, and Plan 5's WebSocket acknowledgements), and
`SystemOrigin` for commands the server issues to itself — deadline
expiries, watchdog re-fires, reaper aborts — where nobody is waiting but
the loop still resolves unconditionally. A nullable origin would mean a
branch on every resolution path in the consumer loop, and the one that got
forgotten would be a hung request.
"""

import asyncio
import logging
from collections.abc import Sequence
from dataclasses import dataclass

from triviador.domain.game.actions import RejectCode
from triviador.domain.game.events import GameEvent
from triviador.services.ports import RuntimeCode

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Accepted:
    events: tuple[GameEvent, ...]


@dataclass(frozen=True)
class Ignored:
    """§6.1's ignore: a stale window, a duplicate, a command the guards
    dropped. Zero events, nothing persisted, nothing broadcast — and
    deliberately *not* an error, because it is a benign race."""


@dataclass(frozen=True)
class Rejected:
    code: RejectCode
    message: str


@dataclass(frozen=True)
class Failed:
    code: RuntimeCode
    message: str


CommandOutcome = Accepted | Ignored | Rejected | Failed


class SystemOrigin:
    """A server-issued command. `label` names the issuer so a rejection
    that should never happen is greppable."""

    def __init__(self, label: str) -> None:
        self._label = label

    def resolve_ok(self, events: Sequence[GameEvent]) -> None:
        return None

    def resolve_noop(self) -> None:
        return None

    def resolve_rejected(self, code: RejectCode, message: str) -> None:
        # A rejection here means the server issued a command the domain
        # refused — a scheduling bug, not a client problem. Nobody is
        # waiting to be told, so the log is the only place it can surface.
        logger.warning("%s command rejected: %s — %s", self._label, code, message)

    def resolve_failed(self, code: RuntimeCode, message: str) -> None:
        logger.warning("%s command failed: %s — %s", self._label, code, message)


class FutureOrigin:
    """Delivers the outcome to an awaiting caller, exactly once.

    `_resolved` rather than `future.done()`: the future can be done for
    reasons this class never caused (a cancelled REST request), and
    "already delivered by me" and "already finished by someone else" are
    different questions. The first outcome wins; a second call is dropped,
    so a runtime bug cannot overwrite a success with a shutdown code.
    """

    def __init__(self) -> None:
        self.future: asyncio.Future[CommandOutcome] = asyncio.get_running_loop().create_future()
        self._resolved = False

    def resolve_ok(self, events: Sequence[GameEvent]) -> None:
        self._deliver(Accepted(tuple(events)))

    def resolve_noop(self) -> None:
        self._deliver(Ignored())

    def resolve_rejected(self, code: RejectCode, message: str) -> None:
        self._deliver(Rejected(code, message))

    def resolve_failed(self, code: RuntimeCode, message: str) -> None:
        self._deliver(Failed(code, message))

    def _deliver(self, outcome: CommandOutcome) -> None:
        if self._resolved:
            return
        self._resolved = True
        try:
            self.future.set_result(outcome)
        except asyncio.InvalidStateError:
            # The awaiting caller vanished — a cancelled HTTP request, most
            # often. §5.2: transport delivery failure is logged and never
            # reaches runtime fault handling. The batch is already durable;
            # destroying a healthy game over a dead socket would be the
            # actual bug.
            logger.debug("origin future already settled; dropping %r", outcome)
