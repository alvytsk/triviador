"""§10.6's two probes.

`live` answers from the process alone. `ready` reports the recorded result
of the startup sequence plus the two things that can change while running:
the database, and any game the manager has given up on.
"""

from fastapi import APIRouter, Response
from pydantic import BaseModel, ConfigDict

from triviador.api.deps import Deps

router = APIRouter(prefix="/api/health", tags=["health"])


class DegradedGame(BaseModel):
    model_config = ConfigDict(extra="forbid")

    game_id: str
    reason: str


class ReadinessReport(BaseModel):
    """Named `ReadinessReport`, not `Readiness`: `deps.Readiness` is the
    mutable record the lifespan writes, and two same-named types one import
    apart is the kind of collision that gets resolved by whichever module
    was imported last."""

    model_config = ConfigDict(extra="forbid")

    database: bool
    migrations_current: bool
    recovery_complete: bool
    garage_ready: bool
    degraded_games: tuple[DegradedGame, ...]


@router.get("/live")
async def live() -> dict[str, str]:
    return {"status": "alive"}


@router.get("/ready")
async def ready(deps: Deps, response: Response) -> ReadinessReport:
    state = deps.readiness
    garage_ready = state.garage_ready
    if not garage_ready:
        # The asymmetric half of "remembered, not probed" below: a latch
        # that is already True is never re-probed (a healthy process must
        # not be taken out of rotation by a hiccup, `Readiness.garage_ready`'s
        # docstring), but a latch that is False needs exactly one more
        # chance to heal, because False can be a purely startup-time
        # accident rather than a real, ongoing outage. `restart:
        # unless-stopped` restarts `backend` and `garage` independently
        # after a host reboot — `depends_on` is enforced by `up`, not by
        # the daemon — so `backend` can win that race and latch `False`
        # before `garage-init` has even run. Nothing else ever clears the
        # latch (no cron, no operator action is documented), so without
        # this the only recovery is a manual `restart backend` — and
        # re-running `infra/deploy.sh` does not do it either, for the same
        # no-recreate-an-unchanged-service reason `provision-media-lock.sh`
        # has to work around. Re-probing only while latched False costs
        # nothing on the healthy path and heals the unlucky-boot-order path
        # on its own, on the very next poll.
        garage_ready = await deps.garage.ready()
        state.garage_ready = garage_ready
    body = ReadinessReport(
        # Probed, not remembered: a flag set at startup reports a database
        # that died an hour ago as reachable, and readiness is the one
        # endpoint whose whole job is to notice.
        database=await deps.database.ping(),
        migrations_current=state.migrations_current,
        recovery_complete=state.recovery_complete,
        # Remembered-while-healthy, not probed — the opposite of
        # `database`, and deliberately so: once `garage_ready` is True it
        # is the result of the startup assertion (`Readiness.garage_ready`'s
        # docstring has the full reasoning) and stays that way without a
        # fresh call to `deps.garage`, so a poll never turns a Garage blip
        # into a backend that removes itself from rotation. Only a *latched
        # False* is re-probed, above — see that comment for why the two
        # halves of "remembered" are not symmetric.
        garage_ready=garage_ready,
        degraded_games=tuple(
            DegradedGame(game_id=str(gid), reason=reason) for gid, reason in deps.manager.degraded()
        ),
    )
    if not (
        body.database and body.migrations_current and body.recovery_complete and body.garage_ready
    ):
        # A degraded game is deliberately *not* part of this condition:
        # §5.6 clears `Failed` only by operator action, and ADR-002 gives
        # the process no peer to fail over to, so taking the whole server
        # out of rotation over one game punishes every other player.
        response.status_code = 503
    return body
