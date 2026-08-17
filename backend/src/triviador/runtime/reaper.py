"""§11.6. Abandoned lobbies get aborted; runtimes nobody needs get unloaded.

Two halves that pull in opposite directions, and both matter:

The abandoned-lobby sweep queries the *database*, not resident runtimes.
A resident scan would miss every lobby the no-connections rule had already
unloaded — which is most of them, after a few hours — and those rows would
stay in `LOBBY` forever. So this loads the runtime *in order to* abort it,
which is also why `AbortGame(actor_id=None)` exists (Plan 2 §3.3): an
empty, abandoned lobby has no participant that could pass guard 3.

The unload sweep is the mirror image: it must never touch a game somebody
is playing. `EXPANSION`/`BATTLE` are never unloaded regardless of
presence — evicting one would orphan its `DeadlineId` and the game would
stop advancing while looking perfectly healthy.
"""

import asyncio
import contextlib
import logging
from datetime import timedelta
from uuid import uuid4

from triviador.domain.game.actions import AbortGame
from triviador.domain.game.state import TERMINAL_PHASES, Phase
from triviador.domain.ids import GameId
from triviador.runtime.errors import RuntimeClosed, ServerBusy
from triviador.runtime.manager import GameManager
from triviador.runtime.origins import SystemOrigin
from triviador.runtime.runtime import GameRuntime, QueuedCommand
from triviador.services.ports import Clock, GameQueriesPort, GameSubscriberControl

logger = logging.getLogger(__name__)


class Reaper:
    """Sweeps every `interval_s` seconds: aborts lobbies the database says
    are abandoned, then unloads whatever resident runtime nobody needs."""

    def __init__(
        self,
        *,
        manager: GameManager,
        games: GameQueriesPort,
        subscribers: GameSubscriberControl,
        clock: Clock,
        interval_s: float = 60.0,
        empty_lobby_grace_minutes: int = 5,
        lobby_max_age_hours: int = 6,
    ) -> None:
        self._manager = manager
        self._games = games
        self._subscribers = subscribers
        self._clock = clock
        self._interval_s = interval_s
        self._empty_lobby_grace = timedelta(minutes=empty_lobby_grace_minutes)
        self._lobby_max_age = timedelta(hours=lobby_max_age_hours)
        self._task: asyncio.Task[None] | None = None

    def start(self) -> None:
        self._task = asyncio.create_task(self._run(), name="reaper")

    async def aclose(self) -> None:
        if self._task is None:
            return
        self._task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await self._task
        self._task = None

    async def _run(self) -> None:
        while True:
            try:
                await self._clock.sleep_until(
                    self._clock.now() + timedelta(seconds=self._interval_s)
                )
            except asyncio.CancelledError:
                return
            try:
                await self.tick()
            except Exception:
                # Matches `Watchdog._run`: one bad tick must not end the
                # reaper for the process's lifetime. `start()` never
                # awaits this task, so an uncaught exception here would
                # otherwise kill it silently — every game in the process
                # would stop being reaped, with only an unlogged "Task
                # exception was never retrieved" to show for it.
                logger.exception("reaper: tick failed; will retry next interval")

    async def tick(self) -> None:
        await self._abort_abandoned_lobbies()
        await self._unload_idle_runtimes()

    async def _abort_abandoned_lobbies(self) -> None:
        now = self._clock.now()
        empty = await self._games.find_empty_lobbies(created_before=now - self._empty_lobby_grace)
        stale = await self._games.find_stale_lobbies(created_before=now - self._lobby_max_age)

        # `dict.fromkeys` rather than a set: a lobby can be both empty and
        # stale, and aborting it twice would mean the second abort lands
        # on an already-aborted game — harmless, since guard 1 drops it,
        # but a command nobody needed to issue and a log line that reads
        # like a bug. `dict.fromkeys` also preserves query order, so the
        # logs read in the order the two queries found them.
        for game_id in dict.fromkeys((*empty, *stale)):
            try:
                self._submit_abort(await self._manager.get(game_id), game_id)
            except (RuntimeClosed, ServerBusy) as exc:
                # The runtime exists but would not take the command right
                # now. The next tick tries again.
                logger.warning("reaper: could not abort lobby %s: %s", game_id, exc)
            except Exception:
                # A lobby that will not load is the manager's problem —
                # it has already been recorded `Failed` or `Recovering`.
                # The other abandoned lobbies still need aborting, so one
                # bad game must not truncate the sweep over the rest.
                logger.exception("reaper: could not load lobby %s", game_id)

    def _submit_abort(self, runtime: GameRuntime, game_id: GameId) -> None:
        runtime.submit(
            QueuedCommand(
                # `actor_id=None`: a system-issued abort. Guard 3
                # validates the actor only when one is present, so this
                # is legal even in an empty lobby — which has no
                # participant that could pass it (Plan 2 §3.3).
                command=AbortGame(actor_id=None),
                operation_id=f"reaper-abort-{game_id}-{uuid4()}",
                origin=SystemOrigin("reaper"),
            )
        )

    async def _unload_idle_runtimes(self) -> None:
        for runtime in self._manager.live_runtimes():
            try:
                await self._unload_if_reapable(runtime)
            except Exception:
                # Matches `Watchdog.tick`'s per-runtime guard: one
                # runtime's `state` or `subscriber_count` misbehaving
                # must not stop the other resident games from being
                # swept this tick.
                logger.exception("reaper: could not evaluate game %s for unload", runtime.game_id)

    async def _unload_if_reapable(self, runtime: GameRuntime) -> None:
        phase = runtime.state.phase
        if phase in TERMINAL_PHASES:
            await self._manager.unload(runtime.game_id)
            return
        if phase is Phase.LOBBY and self._subscribers.subscriber_count(runtime.game_id) == 0:
            await self._manager.unload(runtime.game_id)
            return
        # EXPANSION / BATTLE: never unloaded, regardless of presence.
        # Unloading one would orphan its `DeadlineId`, and the game would
        # stop advancing while looking perfectly healthy (§11.6, and
        # §12.2's presence case: disconnecting the last player must not
        # pause the game).
