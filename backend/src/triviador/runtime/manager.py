"""Every runtime in the process, and the two background tasks.

`GameRuntime` owns exactly one game; `GameManager` owns the dict of them,
the per-game locks that keep it to one runtime per game, the generation
counter, and — from Task 12 — quarantine and recovery.
"""

import asyncio
import itertools
import logging
import random
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from triviador.domain.game.state import GameState
from triviador.domain.ids import GameId
from triviador.runtime.commit import CommandExecutor
from triviador.runtime.errors import (
    GameRecovering,
    GameUnrecoverable,
    PermanentReplayFailure,
    ServerRestarting,
)
from triviador.runtime.materialiser import Materialiser
from triviador.runtime.runtime import GameRuntime
from triviador.services.ports import (
    Broadcaster,
    Clock,
    GameQueriesPort,
    GameSubscriberControl,
    UnitOfWorkPort,
)

logger = logging.getLogger(__name__)


class Loader(Protocol):
    """What `GameManager` asks of `runtime.loader.GameLoader`, structurally.

    `GameLoader` is a concrete class, not a Protocol, so typing this
    parameter against it directly would force every test double —
    `CountingLoader`, and whatever Tasks 12/13 add — to subclass it just
    to be accepted by `mypy --strict`. Declaring the shape here, the same
    way `commit.Executor` does for `CommandExecutor`, lets a stub satisfy
    it by structure instead.
    """

    async def load(self, game_id: GameId) -> GameState: ...


@dataclass(frozen=True)
class Live:
    runtime: GameRuntime


@dataclass(frozen=True)
class Recovering:
    """Callers get `GAME_RECOVERING` (503). Transient faults stay here
    while the backoff runs."""

    attempt: int
    next_at: datetime


@dataclass(frozen=True)
class Failed:
    """Callers get `GAME_UNRECOVERABLE` (503). Logged at error, surfaced
    in `/api/health/ready` as a degraded detail, and cleared only by
    operator action."""

    reason: str


Entry = Live | Recovering | Failed


class GameManager:
    def __init__(
        self,
        *,
        loader: Loader,
        uow: UnitOfWorkPort,
        materialiser: Materialiser,
        clock: Clock,
        broadcaster: Broadcaster,
        subscribers: GameSubscriberControl,
        games: GameQueriesPort,
        rng: random.Random,
        queue_maxsize: int = 256,
        commit_max_attempts: int = 3,
    ) -> None:
        self._loader = loader
        self._uow = uow
        self._materialiser = materialiser
        self._clock = clock
        self._broadcaster = broadcaster
        self._subscribers = subscribers
        self._games = games
        self._rng = rng
        self._queue_maxsize = queue_maxsize
        self._commit_max_attempts = commit_max_attempts
        self._entries: dict[GameId, Entry] = {}
        self._locks: dict[GameId, asyncio.Lock] = {}
        self._generations = itertools.count(1)
        # Set by Task 16's `shutdown`. Declared here because `_load`
        # already reads it — a flag added later to a method written
        # earlier is how the shutdown race got in.
        self._shutting_down = False

    def entry_for(self, game_id: GameId) -> Entry | None:
        return self._entries.get(game_id)

    def live_runtimes(self) -> tuple[GameRuntime, ...]:
        return tuple(e.runtime for e in self._entries.values() if isinstance(e, Live))

    def degraded(self) -> tuple[tuple[GameId, str], ...]:
        """What `/api/health/ready` reports (Plan 5 consumes this)."""
        return tuple((gid, e.reason) for gid, e in self._entries.items() if isinstance(e, Failed))

    async def get(self, game_id: GameId) -> GameRuntime:
        entry = self._entries.get(game_id)
        runtime = self._usable(entry)
        if runtime is not None:
            return runtime

        # The lock is per game, not global: loading one game must not
        # serialize every other game's first join.
        lock = self._locks.setdefault(game_id, asyncio.Lock())
        async with lock:
            # Re-check under the lock. Without this, every waiter that
            # queued behind the first loader would load again.
            runtime = self._usable(self._entries.get(game_id))
            if runtime is not None:
                return runtime
            return await self._load(game_id)

    def _usable(self, entry: Entry | None) -> GameRuntime | None:
        match entry:
            case Live(runtime=runtime) if not runtime.closed:
                return runtime
            case Recovering():
                raise GameRecovering("game is recovering")
            case Failed(reason=reason):
                raise GameUnrecoverable(reason)
            case _:
                # `None` (never loaded) and a `Live` entry whose runtime
                # has since closed (quarantine, or Task 15's unload) both
                # fall through here: neither is a runtime this caller can
                # be handed, so both go back through `_load`.
                return None

    async def _load(self, game_id: GameId) -> GameRuntime:
        # Task 16 adds this guard. It is the one that actually holds: a
        # recovery already inside `_load` when the fence goes up would
        # otherwise install a `Live` runtime after `shutdown()` returned,
        # and the checks in `_recover` never get another turn to notice.
        if self._shutting_down:
            raise ServerRestarting("server is restarting")

        try:
            state = await self._loader.load(game_id)
        except PermanentReplayFailure as exc:
            # Straight to Failed, no backoff: retrying a log that cannot
            # be decoded only hides the incident.
            logger.error("game %s: unrecoverable — %s", game_id, exc)
            self._entries[game_id] = Failed(reason=str(exc))
            raise GameUnrecoverable(str(exc)) from exc
        # Everything else is transient and propagates unwrapped, writing
        # no entry: there is no runtime to tear down and nothing queued,
        # so the caller sees the error and the next `get` tries again
        # from scratch. `Recovering` is for a game that *had* a runtime
        # — that is Task 12.

        runtime = GameRuntime(
            state=state,
            executor=CommandExecutor(
                uow=self._uow,
                materialiser=self._materialiser,
                clock=self._clock,
                rng=self._rng,
                max_attempts=self._commit_max_attempts,
            ),
            clock=self._clock,
            broadcaster=self._broadcaster,
            on_fault=self._on_fault,
            generation=next(self._generations),
            rng=self._rng,
            queue_maxsize=self._queue_maxsize,
        )
        runtime.start()
        self._entries[game_id] = Live(runtime)
        return runtime

    def _on_fault(self, runtime: GameRuntime, exc: BaseException) -> None:
        """Task 12 replaces this with the scheduled quarantine. Until then
        it only logs, so Task 11's registry can be tested on its own."""
        logger.error("game %s: fault reported — %s", runtime.game_id, exc)
