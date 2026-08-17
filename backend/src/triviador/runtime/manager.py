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
from datetime import datetime, timedelta
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
    RuntimeCode,
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
        backoff_initial_s: float = 1.0,
        backoff_max_s: float = 60.0,
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
        self._backoff_initial_s = backoff_initial_s
        self._backoff_max_s = backoff_max_s
        self._entries: dict[GameId, Entry] = {}
        self._locks: dict[GameId, asyncio.Lock] = {}
        self._generations = itertools.count(1)
        # One quarantine task per game, keyed so a second fault report for
        # the same runtime can be recognised and dropped (see `quarantine`)
        # rather than racing the first teardown.
        self._quarantines: dict[GameId, asyncio.Task[None]] = {}
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

    async def recover_active_games(self) -> tuple[GameId, ...]:
        """Load every game the database says is still being played.

        §11.6 forbids evicting active games — nobody would own their
        `DeadlineId` — and a process restart is exactly an eviction.
        Without this, every deploy pauses every live game until a player
        happens to reconnect, and nothing on the server side shows it.

        `find_unfinished` returns `status IN ('expansion', 'battle')`.
        `FinalTiebreak` is inside `battle`; there is no `final` status.
        Lobbies are deliberately excluded: they hold no deadline, so
        loading them at boot would be work with no owner and no timer,
        and the reaper reaches the abandoned ones through the database
        anyway.

        One unloadable game must not stop the rest, so failures are
        collected and returned rather than raised. `_load` has already
        recorded `Failed` for the permanent ones.

        A shutdown racing this sweep — a SIGTERM landing mid-boot — is
        not one of those failures. `_load` raises `ServerRestarting`
        from its own fence, and that propagates here rather than being
        folded into the returned tuple: a caller that logs the returned
        ids at error would otherwise report every game the sweep never
        reached as "unrecoverable", when the true state is "not yet
        looked at, because the process is exiting". The caller — Plan
        5's startup hook — is expected to treat this the same way
        `_load` itself does: abandon the attempt, not retry it.
        """
        unloadable: list[GameId] = []
        for game_id in await self._games.find_unfinished():
            try:
                await self.get(game_id)
            except ServerRestarting:
                # The fence, not a per-game failure: the server is going
                # away, not this one game. Re-raised rather than caught by
                # `except Exception` below, so it aborts the sweep instead
                # of being reported as N unloadable games.
                raise
            except Exception as exc:
                logger.error("game %s: could not be recovered at startup — %s", game_id, exc)
                unloadable.append(game_id)
        return tuple(unloadable)

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
        """Called from inside the faulting consumer task, so it may only
        *schedule*. §5.6: quarantine is "scheduled onto the manager and
        never run by the faulting consumer task" — a task cannot cancel
        and await itself."""
        self.quarantine(runtime, str(exc))

    def quarantine(self, runtime: GameRuntime, reason: str) -> None:
        """Schedules teardown of `runtime` and recovery of its game.

        Never awaited by its caller: `_on_fault` calls this from inside
        the consumer task it is about to ask to cancel, and Plan 5's
        operator endpoint calls it from an HTTP handler that must not
        block on a full teardown-and-reload. Both get a task they can
        walk away from.
        """
        existing = self._quarantines.get(runtime.game_id)
        if existing is not None and not existing.done():
            # Already tearing this game down. A second report — from the
            # deadline task, or a caller that raced the first — must not
            # start a second teardown that would destroy the replacement
            # generation the first one is about to install.
            return
        self._quarantines[runtime.game_id] = asyncio.create_task(
            self._quarantine(runtime, reason), name=f"quarantine:{runtime.game_id}"
        )

    async def _quarantine(self, runtime: GameRuntime, reason: str) -> None:
        """§5.6's teardown, in order, under the per-game lock: detach from
        the registry, mark closed, drain the queue (every origin gets
        `GAME_RECOVERING`), cancel the consumer and deadline tasks, close
        the sockets through the port, then hand off to `_recover` — which
        re-acquires the lock itself, once per attempt, so a `get` racing
        the backoff sees `Recovering` rather than blocking on it.
        """
        game_id = runtime.game_id
        lock = self._locks.setdefault(game_id, asyncio.Lock())
        async with lock:
            entry = self._entries.get(game_id)
            if isinstance(entry, Live) and entry.runtime is not runtime:
                # A newer generation is already installed; this report is
                # about a runtime nobody can reach any more.
                return

            logger.error(
                "game %s: quarantining generation %d — %s", game_id, runtime.generation, reason
            )
            # Detach from the registry first: no caller handed a `Live`
            # entry from this point on can still be pointed at `runtime`.
            self._entries[game_id] = Recovering(attempt=1, next_at=self._clock.now())
            runtime.closed = True
            runtime.drain(RuntimeCode.GAME_RECOVERING, "game is recovering")
            await runtime.aclose()
            # Through the port: the sockets stay owned by the hub, which
            # is the only thing that knows how to close one.
            self._subscribers.close_game_subscribers(game_id, 1011)
            if self._shutting_down:
                # Tear down, but do not start a recovery the process has
                # no intention of finishing.
                del self._entries[game_id]
                return

        await self._recover(game_id)

    async def _recover(self, game_id: GameId) -> None:
        """Bounded exponential backoff, capped and jittered, retried for as
        long as the fault looks transient.

        Attempts are not capped — a database that is down for ten minutes
        must not leave every game in that window permanently `Failed`,
        because nothing about it is permanent. What is capped is the
        *delay*, so a long outage settles into a steady retry rather than
        an ever-growing one.
        """
        attempt = 1
        # Bound before the loop: mypy cannot see that the only path
        # reaching `sleep_until` below is the one that assigned `next_at`
        # inside the `except`.
        next_at = self._clock.now()
        while True:
            lock = self._locks.setdefault(game_id, asyncio.Lock())
            async with lock:
                if self._shutting_down:
                    # The fence. Without this the loop outlives
                    # `shutdown()` and installs a fresh `Live` runtime
                    # into a registry the process has finished with.
                    return
                if not isinstance(self._entries.get(game_id), Recovering):
                    return  # someone unloaded or replaced this game
                try:
                    await self._load(game_id)
                    logger.info("game %s: recovered on attempt %d", game_id, attempt)
                    return
                except GameUnrecoverable:
                    return  # `_load` already recorded Failed
                except Exception as exc:
                    delay = min(self._backoff_max_s, self._backoff_initial_s * (2 ** (attempt - 1)))
                    delay = self._rng.uniform(0.0, delay)
                    attempt += 1
                    next_at = self._clock.now() + timedelta(seconds=delay)
                    self._entries[game_id] = Recovering(attempt=attempt, next_at=next_at)
                    logger.warning(
                        "game %s: recovery attempt %d failed (%s); retrying at %s",
                        game_id,
                        attempt - 1,
                        exc,
                        next_at,
                    )
            # Outside the lock: a `get` during the wait must be able to
            # observe `Recovering` and answer 503 rather than block.
            await self._clock.sleep_until(next_at)
