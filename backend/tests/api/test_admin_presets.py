from typing import Any

import httpx
import pytest

pytestmark = pytest.mark.asyncio

QUICK: dict[str, Any] = {
    "name": "Quick",
    "is_default": False,
    "rules": {
        "player_count": 3,
        "expansion_rounds": 2,
        "battle_rounds": 2,
        "base_hp": 3,
        "answer_timeout_ms": 20000,
        "pick_timeout_ms": 15000,
        "warmup_ms": 5000,
        "claims_by_rank": [2, 1, 0],
        "pts_base": 1000,
        "pts_territory": 200,
        "pts_conquered": 400,
        "pts_defense": 100,
    },
}


async def test_a_player_cannot_write_presets(signed_in: httpx.AsyncClient) -> None:
    assert (await signed_in.post("/api/admin/presets", json=QUICK)).status_code == 403


async def test_create_and_list(admin_client: httpx.AsyncClient) -> None:
    created = await admin_client.post("/api/admin/presets", json=QUICK)
    assert created.status_code == 201
    listed = (await admin_client.get("/api/admin/presets")).json()
    assert {p["name"] for p in listed} >= {"Quick", "Default"}


async def test_invalid_rules_are_rejected_with_the_domain_s_own_reasons(
    admin_client: httpx.AsyncClient,
) -> None:
    """`validate_rules` is the single source of what a legal ruleset is
    (Plan 2). Re-stating its rules in a Pydantic model would be a second
    copy that drifts; the route calls it and reports what it says."""
    body = {**QUICK, "rules": {**QUICK["rules"], "claims_by_rank": [2, 1]}}
    response = await admin_client.post("/api/admin/presets", json=body)
    assert response.status_code == 422
    assert "claims_by_rank" in response.json()["message"]


async def test_making_a_preset_default_demotes_the_previous_one(
    admin_client: httpx.AsyncClient,
) -> None:
    """`uq_rule_presets_single_default` is a partial unique index (Plan 3):
    without demoting the old default in the same transaction, this is an
    IntegrityError, i.e. a 503 on a legitimate action."""
    created = (
        await admin_client.post("/api/admin/presets", json={**QUICK, "is_default": True})
    ).json()
    listed = (await admin_client.get("/api/admin/presets")).json()
    defaults = [p["id"] for p in listed if p["is_default"]]
    assert defaults == [created["id"]]


async def test_the_default_cannot_be_cleared_by_a_patch(admin_client: httpx.AsyncClient) -> None:
    """The database enforces *at most* one default; "never zero" is ours,
    and `deactivate` is not the only door into it. Clearing the flag here
    would leave `POST /api/games` answering `no_default_preset` to every
    player until someone noticed."""
    default = next(
        p for p in (await admin_client.get("/api/admin/presets")).json() if p["is_default"]
    )
    response = await admin_client.patch(
        f"/api/admin/presets/{default['id']}",
        json={"name": default["name"], "is_default": False, "rules": default["rules"]},
    )
    assert response.status_code == 409
    assert response.json()["code"] == "default_preset"


async def test_a_retired_preset_cannot_be_promoted_to_default(
    admin_client: httpx.AsyncClient,
) -> None:
    """`get_default()` filters on `is_active`, so an inactive default is a
    default nothing can read — the same outage as having none."""
    created = (await admin_client.post("/api/admin/presets", json=QUICK)).json()
    await admin_client.delete(f"/api/admin/presets/{created['id']}")
    response = await admin_client.patch(
        f"/api/admin/presets/{created['id']}",
        json={**QUICK, "is_default": True},
    )
    assert response.status_code == 409
    assert response.json()["code"] == "default_preset"


async def test_the_default_preset_cannot_be_deleted(admin_client: httpx.AsyncClient) -> None:
    """Spec 1B §6.1: DELETE is a soft deactivation and returns 409 for the
    default — "never zero defaults" is application logic the database
    cannot express."""
    default = next(
        p for p in (await admin_client.get("/api/admin/presets")).json() if p["is_default"]
    )
    response = await admin_client.delete(f"/api/admin/presets/{default['id']}")
    assert response.status_code == 409
    assert response.json()["code"] == "default_preset"


async def test_deleting_a_preset_is_a_soft_deactivation(admin_client: httpx.AsyncClient) -> None:
    created = (await admin_client.post("/api/admin/presets", json=QUICK)).json()
    assert (await admin_client.delete(f"/api/admin/presets/{created['id']}")).status_code == 204
    listed = (await admin_client.get("/api/admin/presets")).json()
    assert [p["is_active"] for p in listed if p["id"] == created["id"]] == [False]


async def test_coverage_reports_need_and_bank_per_kind(admin_client: httpx.AsyncClient) -> None:
    """§10.6's table, as numbers. `required_question_budget` is the domain
    function `StartGame` itself uses, so the informative answer here and
    the authoritative one at start time cannot disagree about the need —
    only about the bank, which is the point."""
    created = (await admin_client.post("/api/admin/presets", json=QUICK)).json()
    coverage = (await admin_client.get(f"/api/admin/presets/{created['id']}/coverage")).json()
    assert coverage["required"] == {"numeric": 9, "multiple_choice": 6}
    assert set(coverage["bank"]) == {"numeric", "multiple_choice"}
    assert isinstance(coverage["sufficient"], bool)


async def test_a_retired_presets_detail_and_coverage_are_still_reachable(
    admin_client: httpx.AsyncClient,
) -> None:
    """Fix round 1: `list_all` already shows a retired preset with
    `is_active: false` — a detail view (or a bookmarked
    `/admin/presets/{id}`) that 404s on exactly those rows would make that
    field unreachable except through the list response."""
    created = (await admin_client.post("/api/admin/presets", json=QUICK)).json()
    assert (
        await admin_client.delete(f"/api/admin/presets/{created['id']}")
    ).status_code == 204

    detail = await admin_client.get(f"/api/admin/presets/{created['id']}")
    assert detail.status_code == 200
    assert detail.json()["is_active"] is False

    coverage = await admin_client.get(f"/api/admin/presets/{created['id']}/coverage")
    assert coverage.status_code == 200
