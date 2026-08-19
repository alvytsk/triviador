"""§10.6. Two probes with deliberately different dependencies.

A liveness probe that touches the database restarts a healthy process
during a database blip — which is how a five-second outage becomes a
five-minute one.
"""

import httpx

from tests.api.fakes import FakeDatabase
from triviador.api.deps import AppDependencies


async def test_liveness_is_true_before_anything_is_ready(client: httpx.AsyncClient) -> None:
    response = await client.get("/api/health/live")
    assert response.status_code == 200
    assert response.json() == {"status": "alive"}


async def test_liveness_never_touches_the_database(
    client: httpx.AsyncClient, deps: AppDependencies
) -> None:
    assert isinstance(deps.database, FakeDatabase)
    deps.database.reachable = False
    assert (await client.get("/api/health/live")).status_code == 200
    assert deps.database.pings == 0


async def test_readiness_is_503_until_startup_recovery_finishes(
    client: httpx.AsyncClient, deps: AppDependencies
) -> None:
    """§10.5's order: migrate, then recover, then serve. Reporting ready
    before recovery has finished means a load balancer sends a player to a
    process whose games have no owner and no timer."""
    deps.readiness.recovery_complete = False
    response = await client.get("/api/health/ready")
    assert response.status_code == 503
    # Not `["details"]["recovery_complete"]`: §10.6's 503 path returns the
    # same `ReadinessReport` model as the 200 path, not an error envelope
    # (see `http/health.py`'s `ready` docstring) — a probe wants the
    # checklist at the top level, not a code with the checklist nested
    # under it.
    assert response.json()["recovery_complete"] is False


async def test_a_ready_process_reports_each_check(client: httpx.AsyncClient) -> None:
    response = await client.get("/api/health/ready")
    assert response.status_code == 200
    body = response.json()
    assert body["database"] is True
    assert body["migrations_current"] is True
    assert body["recovery_complete"] is True
    assert body["degraded_games"] == []


async def test_a_failed_game_is_reported_as_a_degraded_detail_without_failing_readiness(
    client: httpx.AsyncClient, deps: AppDependencies
) -> None:
    """§5.6: `Failed` is cleared only by operator action. One unrecoverable
    game must be visible, but it must not take the whole process out of
    rotation — the other games are fine and there is nowhere else to send
    their players (ADR-002: one application process)."""
    from triviador.domain.ids import GameId
    from triviador.runtime.manager import Failed

    deps.manager._entries[GameId("g9")] = Failed(reason="stream will never decode")
    response = await client.get("/api/health/ready")
    assert response.status_code == 200
    assert response.json()["degraded_games"] == [
        {"game_id": "g9", "reason": "stream will never decode"}
    ]


async def test_readiness_reports_a_database_that_went_away_after_startup(
    client: httpx.AsyncClient, deps: AppDependencies
) -> None:
    """The failure a startup-time flag cannot see: the process booted fine
    and PostgreSQL died an hour later. §10.6 asks for "database reachable",
    present tense, so the probe runs per request."""
    assert (await client.get("/api/health/ready")).status_code == 200
    assert isinstance(deps.database, FakeDatabase)
    deps.database.reachable = False
    response = await client.get("/api/health/ready")
    assert response.status_code == 503
    assert response.json()["database"] is False
