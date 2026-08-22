import httpx
import pytest

from tests.api.fakes import RecordingHub
from triviador.api.deps import AppDependencies

pytestmark = pytest.mark.asyncio


async def test_a_player_cannot_list_users(signed_in: httpx.AsyncClient) -> None:
    assert (await signed_in.get("/api/admin/users")).status_code == 403


async def test_deactivating_a_user_closes_their_sockets_with_4401(
    admin_client: httpx.AsyncClient, deps: AppDependencies
) -> None:
    """§10.5: "Deactivation kills sessions immediately — precisely why §7
    chose opaque tokens." The REST half revokes; the socket half is
    `Hub.close_sessions`, which Plan 5 built for this caller."""
    response = await admin_client.post("/api/admin/users/u1/deactivate")
    assert response.status_code == 200
    assert response.json()["is_active"] is False
    assert isinstance(deps.hub, RecordingHub)
    assert deps.hub.closed == [(("s1",), 4401)]


async def test_a_deactivated_user_cannot_use_their_cookie_again(
    admin_client: httpx.AsyncClient, signed_in: httpx.AsyncClient
) -> None:
    """The session resolver joins `users.is_active` (Plan 5's
    `SessionRepository.resolve`), so this holds even for a request already
    in flight behind the revocation."""
    await admin_client.post("/api/admin/users/u1/deactivate")
    assert (await signed_in.get("/api/auth/me")).status_code == 401


async def test_an_admin_cannot_deactivate_themselves(admin_client: httpx.AsyncClient) -> None:
    response = await admin_client.post("/api/admin/users/admin/deactivate")
    assert response.status_code == 409
    assert response.json()["code"] == "self_target"


async def test_the_last_admin_cannot_be_demoted(admin_client: httpx.AsyncClient) -> None:
    response = await admin_client.post("/api/admin/users/admin/role", json={"role": "player"})
    assert response.status_code == 409
    assert response.json()["code"] == "last_admin"


async def test_promoting_then_demoting_is_allowed(
    admin_client: httpx.AsyncClient, deps: AppDependencies
) -> None:
    assert (
        await admin_client.post("/api/admin/users/u1/role", json={"role": "admin"})
    ).status_code == 200
    demoted = await admin_client.post("/api/admin/users/admin/role", json={"role": "player"})
    assert demoted.status_code == 200
    assert demoted.json()["role"] == "player"


async def test_demotion_also_closes_that_user_s_sockets(
    admin_client: httpx.AsyncClient, deps: AppDependencies
) -> None:
    """A socket opened as an admin keeps its `AuthenticatedPrincipal` for
    the life of the connection (§6.5), so a demotion that left it open
    would leave admin standing behind on a live connection."""
    await admin_client.post("/api/admin/users/u1/role", json={"role": "admin"})
    await admin_client.post("/api/admin/users/u1/role", json={"role": "player"})
    assert isinstance(deps.hub, RecordingHub)
    assert (("s1",), 4401) in deps.hub.closed
