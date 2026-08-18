"""The runtime's own exception surface.

Split from `db/errors.py` on purpose: `runtime/` may not import `db/`, and
these describe scheduling and lifecycle conditions rather than storage
ones. `ServerBusy` and `RuntimeClosed` are raised *out of* `submit`, at
which point the caller still owns the origin; everything else is raised
inside the runtime and never crosses back out.
"""


class RuntimeClosed(Exception):
    """`submit` was called on a runtime that has been quarantined,
    unloaded, or shut down. The caller re-`get()`s the game (§5.6)."""


class ServerBusy(Exception):
    """The command queue is full. `submit` rejects rather than blocking —
    its caller is a WebSocket read loop that must not stall (§5.6)."""


class ServerRestarting(Exception):
    """The manager has stopped accepting new commands (§5.6 shutdown)."""


class GameRecovering(Exception):
    """The registry entry is `Recovering`. Callers see 503 (§5.6)."""


class GameUnrecoverable(Exception):
    """The registry entry is `Failed`: replay will never succeed, so this
    is not retried and is cleared only by operator action (§5.6)."""


class PermanentReplayFailure(Exception):
    """The event log cannot be replayed into a `GameState`, and no amount
    of retrying will change that: an unknown wire type with no upcaster, a
    decode failure, a `map_sha256` mismatch. Sends the registry entry
    straight to `Failed` without backoff, because retrying would only hide
    the incident (§5.6)."""


class CommitFault(Exception):
    """One command attempt failed in a way that quarantines the runtime:
    persistence unavailable after retries, an exception out of
    `decide`/`evolve`, a database error in the materialiser, a
    `ConcurrentModification`, or a reconciliation mismatch (§5.5)."""
