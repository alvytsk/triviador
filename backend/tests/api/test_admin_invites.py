import httpx
import pytest

pytestmark = pytest.mark.asyncio


async def test_a_player_cannot_issue_invites(signed_in: httpx.AsyncClient) -> None:
    assert (await signed_in.post("/api/admin/invites", json={"count": 1})).status_code == 403


async def test_issuing_returns_the_codes_exactly_once(admin_client: httpx.AsyncClient) -> None:
    """`invite_codes.code_hash` is a SHA-256 (Plan 3): the plaintext exists
    only in this response. Listing them later returns status, never a
    code — a list endpoint that could re-read them would make the hash
    decorative."""
    issued = await admin_client.post(
        "/api/admin/invites", json={"count": 3, "expires_in_hours": 48}
    )
    assert issued.status_code == 201
    codes = [item["code"] for item in issued.json()]
    assert len(set(codes)) == 3

    listed = await admin_client.get("/api/admin/invites")
    assert listed.status_code == 200
    assert all("code" not in item for item in listed.json())
    assert {item["status"] for item in listed.json()} == {"pending"}


async def test_an_issued_code_can_be_redeemed(
    admin_client: httpx.AsyncClient, client: httpx.AsyncClient
) -> None:
    """The end-to-end fact this route exists for: `POST /api/auth/redeem`
    is public and takes exactly what was printed here."""
    code = (await admin_client.post("/api/admin/invites", json={"count": 1})).json()[0]["code"]
    redeemed = await client.post(
        "/api/auth/redeem",
        json={
            "code": code,
            "username": "newcomer",
            "password": "correct horse",
            "display_name": "Newcomer",
        },
    )
    assert redeemed.status_code == 201


async def test_a_revoked_code_cannot_be_redeemed(
    admin_client: httpx.AsyncClient, client: httpx.AsyncClient
) -> None:
    issued = (await admin_client.post("/api/admin/invites", json={"count": 1})).json()[0]
    assert (await admin_client.post(f"/api/admin/invites/{issued['id']}/revoke")).status_code == 200
    redeemed = await client.post(
        "/api/auth/redeem",
        json={
            "code": issued["code"],
            "username": "late",
            "password": "correct horse",
            "display_name": "Late",
        },
    )
    assert redeemed.status_code == 401
    assert redeemed.json()["code"] == "invite_invalid"


async def test_revoking_twice_is_not_an_error(admin_client: httpx.AsyncClient) -> None:
    issued = (await admin_client.post("/api/admin/invites", json={"count": 1})).json()[0]
    await admin_client.post(f"/api/admin/invites/{issued['id']}/revoke")
    second = await admin_client.post(f"/api/admin/invites/{issued['id']}/revoke")
    assert second.status_code == 200
    assert second.json()["status"] == "revoked"


async def test_the_count_is_bounded(admin_client: httpx.AsyncClient) -> None:
    assert (await admin_client.post("/api/admin/invites", json={"count": 0})).status_code == 422
    assert (await admin_client.post("/api/admin/invites", json={"count": 501})).status_code == 422
