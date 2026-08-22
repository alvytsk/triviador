import httpx
import pytest

from tests.api.test_admin_presets import QUICK

pytestmark = pytest.mark.asyncio


async def test_an_anonymous_visitor_gets_401(client: httpx.AsyncClient) -> None:
    assert (await client.get("/api/presets")).status_code == 401


async def test_a_player_sees_active_presets_without_admin(signed_in: httpx.AsyncClient) -> None:
    """The deviation this plan states in Decision 1: without it, `POST
    /api/games` accepts a `preset_id` no player could ever learn."""
    response = await signed_in.get("/api/presets")
    assert response.status_code == 200
    body = response.json()
    assert body and {"id", "name", "is_default", "rules"} <= set(body[0])


async def test_a_deactivated_preset_is_not_listed(
    signed_in: httpx.AsyncClient, admin_client: httpx.AsyncClient
) -> None:
    created = (await admin_client.post("/api/admin/presets", json=QUICK)).json()
    await admin_client.delete(f"/api/admin/presets/{created['id']}")
    assert created["id"] not in {p["id"] for p in (await signed_in.get("/api/presets")).json()}
