"""§6.1: `GET /api/maps/{id}` returns region ids, display names, and
`svg_url` — **never** adjacency."""

import httpx


async def test_listing_maps_requires_a_session(client: httpx.AsyncClient) -> None:
    assert (await client.get("/api/maps")).status_code == 401


async def test_the_registry_is_listed(signed_in: httpx.AsyncClient) -> None:
    response = await signed_in.get("/api/maps")
    assert response.status_code == 200
    assert [m["map_id"] for m in response.json()] == ["grid"]
    assert response.json()[0]["region_count"] == 9


async def test_a_map_carries_its_regions_and_an_svg_url(signed_in: httpx.AsyncClient) -> None:
    response = await signed_in.get("/api/maps/grid")
    body = response.json()
    assert body["svg_url"] == "/maps/grid/map.svg"
    assert {r["region_id"] for r in body["regions"]} == {f"r{i}" for i in range(9)}
    assert body["regions"][0]["display_name"] == "R0"


async def test_adjacency_is_never_returned(signed_in: httpx.AsyncClient) -> None:
    """§8.8's reason: the client is told its options, not the rule that
    produced them. Adjacency lives in `domain/maps` alone, and a client
    that had it would be holding a fragment of the ruleset that can drift."""
    text = (await signed_in.get("/api/maps/grid")).text
    assert "adjacency" not in text
    assert "neighbours" not in text


async def test_an_unknown_map_is_404_with_its_own_code(signed_in: httpx.AsyncClient) -> None:
    response = await signed_in.get("/api/maps/atlantis")
    assert response.status_code == 404
    assert response.json()["code"] == "map_unknown"
