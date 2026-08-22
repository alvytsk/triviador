"""§10.6. Two probes with deliberately different dependencies.

A liveness probe that touches the database restarts a healthy process
during a database blip — which is how a five-second outage becomes a
five-minute one.
"""

import httpx

from tests.api.fakes import FakeDatabase, FakeGarageProbe
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
    assert body["garage_ready"] is True
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


async def test_readiness_reports_garage_initialisation(
    client: httpx.AsyncClient, deps: AppDependencies
) -> None:
    """§10.6's fourth condition. The backend verifies at startup that its
    buckets exist and that the staging bucket is not website-enabled; a
    deploy where garage-init silently did not run must not report ready.

    A `False` latch is re-probed (see `test_readiness_reprobes_garage_
    when_latched_false_and_heals`), so the underlying probe must genuinely
    still fail here — this is the case where garage-init really never ran,
    not the transient-startup-race case that is supposed to heal."""
    deps.readiness.garage_ready = False
    assert isinstance(deps.garage, FakeGarageProbe)
    deps.garage.ready_result = False

    response = await client.get("/api/health/ready")

    assert response.status_code == 503
    assert response.json()["garage_ready"] is False


async def test_readiness_does_not_probe_garage_per_poll(
    client: httpx.AsyncClient, deps: AppDependencies
) -> None:
    """Reporting the recorded startup result, not re-probing: a probe on
    every poll turns a Garage blip into a backend that takes itself out of
    rotation, which is the failure §10.6 explicitly rejects."""
    deps.readiness.garage_ready = True
    assert isinstance(deps.garage, FakeGarageProbe)
    before = deps.garage.calls

    response = await client.get("/api/health/ready")

    assert response.status_code == 200
    assert deps.garage.calls == before


async def test_readiness_reprobes_garage_when_latched_false_and_heals(
    client: httpx.AsyncClient, deps: AppDependencies
) -> None:
    """The asymmetric other half of `test_readiness_does_not_probe_garage_
    per_poll`: a `False` latch is exactly the transient-at-startup case
    (`backend` winning the reboot race against `garage-init`) and must heal
    on its own, without a manual `restart backend`. A `True` latch must
    never be reprobed — proven by the sibling test above; this one proves
    `False` is reprobed every poll until Garage answers `True`, and that
    once it does, the latch is set and polling stops touching the probe
    again."""
    deps.readiness.garage_ready = False
    assert isinstance(deps.garage, FakeGarageProbe)
    deps.garage.ready_result = False
    deps.garage.calls = 0

    first = await client.get("/api/health/ready")
    assert first.status_code == 503
    assert first.json()["garage_ready"] is False
    assert deps.garage.calls == 1

    deps.garage.ready_result = True
    second = await client.get("/api/health/ready")
    assert second.status_code == 200
    assert second.json()["garage_ready"] is True
    assert deps.garage.calls == 2
    assert deps.readiness.garage_ready is True

    third = await client.get("/api/health/ready")
    assert third.status_code == 200
    assert deps.garage.calls == 2  # healed: no longer reprobed


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
