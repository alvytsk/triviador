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
    degraded_games: tuple[DegradedGame, ...]


@router.get("/live")
async def live() -> dict[str, str]:
    return {"status": "alive"}


@router.get("/ready")
async def ready(deps: Deps, response: Response) -> ReadinessReport:
    state = deps.readiness
    body = ReadinessReport(
        # Probed, not remembered: a flag set at startup reports a database
        # that died an hour ago as reachable, and readiness is the one
        # endpoint whose whole job is to notice.
        database=await deps.database.ping(),
        migrations_current=state.migrations_current,
        recovery_complete=state.recovery_complete,
        degraded_games=tuple(
            DegradedGame(game_id=str(gid), reason=reason) for gid, reason in deps.manager.degraded()
        ),
    )
    if not (body.database and body.migrations_current and body.recovery_complete):
        # A degraded game is deliberately *not* part of this condition:
        # §5.6 clears `Failed` only by operator action, and ADR-002 gives
        # the process no peer to fail over to, so taking the whole server
        # out of rotation over one game punishes every other player.
        response.status_code = 503
    return body
