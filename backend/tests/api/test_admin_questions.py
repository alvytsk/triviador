from typing import Any

import httpx
import pytest

from tests.api.fakes import FakeQuestionAdmin
from triviador.api.deps import AppDependencies

pytestmark = pytest.mark.asyncio


async def test_a_player_cannot_list_questions(signed_in: httpx.AsyncClient) -> None:
    assert (await signed_in.get("/api/admin/questions")).status_code == 403


async def test_the_list_answers_a_page_and_a_total(admin_client: httpx.AsyncClient) -> None:
    response = await admin_client.get("/api/admin/questions?limit=1")
    assert response.status_code == 200
    body = response.json()
    assert len(body["items"]) == 1
    assert body["total"] >= 1
    assert body["limit"] == 1 and body["offset"] == 0


async def test_the_filters_reach_the_repository(
    admin_client: httpx.AsyncClient, deps: AppDependencies
) -> None:
    await admin_client.get(
        "/api/admin/questions?kind=numeric&is_active=true&has_media=false&q=velvet"
    )
    assert isinstance(deps.questions_admin, FakeQuestionAdmin)
    assert deps.questions_admin.last_filters is not None
    assert deps.questions_admin.last_filters.kind == "numeric"
    assert deps.questions_admin.last_filters.is_active is True
    assert deps.questions_admin.last_filters.has_media is False
    assert deps.questions_admin.last_filters.search == "velvet"


async def test_an_unknown_kind_is_a_validation_error_not_an_empty_page(
    admin_client: httpx.AsyncClient,
) -> None:
    """A typo in a filter must not look like an empty bank."""
    response = await admin_client.get("/api/admin/questions?kind=picture")
    assert response.status_code == 422
    assert response.json()["code"] == "validation_failed"


async def test_limit_is_bounded(admin_client: httpx.AsyncClient) -> None:
    assert (await admin_client.get("/api/admin/questions?limit=5000")).status_code == 422


async def test_a_missing_question_is_404(admin_client: httpx.AsyncClient) -> None:
    response = await admin_client.get("/api/admin/questions/nope")
    assert response.status_code == 404
    assert response.json()["code"] == "not_found"


MC_BODY: dict[str, Any] = {
    "kind": "multiple_choice",
    "prompt": "Which river runs through Prague?",
    "category_id": "cat-1",
    "difficulty": "easy",
    "media_asset_id": None,
    "choices": [
        {"text": "Vltava", "is_correct": True},
        {"text": "Elbe", "is_correct": False},
        {"text": "Morava", "is_correct": False},
        {"text": "Ohře", "is_correct": False},
    ],
    "numeric_answer": None,
    "unit": None,
}


async def test_creating_a_question_answers_201_with_the_saved_question(
    admin_client: httpx.AsyncClient,
) -> None:
    response = await admin_client.post("/api/admin/questions", json=MC_BODY)
    assert response.status_code == 201
    assert response.json()["question"]["prompt"] == MC_BODY["prompt"]
    assert response.json()["duplicate_of"] == []


async def test_three_choices_is_a_validation_error(admin_client: httpx.AsyncClient) -> None:
    body = {**MC_BODY, "choices": MC_BODY["choices"][:3]}
    response = await admin_client.post("/api/admin/questions", json=body)
    assert response.status_code == 422
    assert response.json()["code"] == "validation_failed"


async def test_two_correct_choices_is_a_validation_error(admin_client: httpx.AsyncClient) -> None:
    choices = [dict(c) for c in MC_BODY["choices"]]
    choices[1]["is_correct"] = True
    response = await admin_client.post("/api/admin/questions", json={**MC_BODY, "choices": choices})
    assert response.status_code == 422


async def test_a_duplicate_prompt_is_a_warning_and_still_saves(
    admin_client: httpx.AsyncClient,
) -> None:
    """§10.2: legitimately similar phrasings exist, so the duplicate hash
    surfaces as a field on a 201, never as a 409."""
    first = await admin_client.post("/api/admin/questions", json=MC_BODY)
    second = await admin_client.post(
        "/api/admin/questions", json={**MC_BODY, "prompt": "  which river RUNS through prague? "}
    )
    assert second.status_code == 201
    assert second.json()["duplicate_of"] == [first.json()["question"]["id"]]


async def test_patching_a_missing_question_is_404(admin_client: httpx.AsyncClient) -> None:
    assert (await admin_client.patch("/api/admin/questions/nope", json=MC_BODY)).status_code == 404


async def test_deactivate_and_activate_flip_the_flag_without_bumping_version(
    admin_client: httpx.AsyncClient,
) -> None:
    """Both directions, because §10.2 puts `is_active` in the editor and a
    bank whose rows can never be deleted (§7) needs retirement to be
    reversible. Neither touches `version` — Spec 1 §7 again."""
    created = (await admin_client.post("/api/admin/questions", json=MC_BODY)).json()["question"]
    off = await admin_client.post(f"/api/admin/questions/{created['id']}/deactivate")
    assert off.status_code == 200
    assert (off.json()["is_active"], off.json()["version"]) == (False, created["version"])
    on = await admin_client.post(f"/api/admin/questions/{created['id']}/activate")
    assert (on.json()["is_active"], on.json()["version"]) == (True, created["version"])


async def test_creating_with_a_stale_category_id_is_404_not_503(
    admin_client: httpx.AsyncClient, deps: AppDependencies
) -> None:
    """Important #1: a foreign-key violation on `questions.category_id`
    must read as "that category is gone", not as "the database is
    down"."""
    assert isinstance(deps.questions_admin, FakeQuestionAdmin)
    deps.questions_admin.missing_category_ids = frozenset({MC_BODY["category_id"]})
    response = await admin_client.post("/api/admin/questions", json=MC_BODY)
    assert response.status_code == 404
    assert response.json()["code"] == "not_found"


async def test_creating_with_a_stale_media_asset_id_is_404_not_503(
    admin_client: httpx.AsyncClient, deps: AppDependencies
) -> None:
    """The other half of Important #1: `media-gc` deletes a
    `media_assets` row an editor tab still has open, and posting that
    stale id must not read as a database outage either."""
    assert isinstance(deps.questions_admin, FakeQuestionAdmin)
    deps.questions_admin.missing_media_asset_ids = frozenset({"stale-asset"})
    body = {**MC_BODY, "media_asset_id": "stale-asset"}
    response = await admin_client.post("/api/admin/questions", json=body)
    assert response.status_code == 404
    assert response.json()["code"] == "not_found"


async def test_patching_with_a_stale_category_or_media_asset_id_is_404_not_503(
    admin_client: httpx.AsyncClient, deps: AppDependencies
) -> None:
    assert isinstance(deps.questions_admin, FakeQuestionAdmin)
    created = (await admin_client.post("/api/admin/questions", json=MC_BODY)).json()["question"]

    deps.questions_admin.missing_category_ids = frozenset({MC_BODY["category_id"]})
    by_category = await admin_client.patch(f"/api/admin/questions/{created['id']}", json=MC_BODY)
    assert by_category.status_code == 404
    assert by_category.json()["code"] == "not_found"

    deps.questions_admin.missing_category_ids = frozenset()
    deps.questions_admin.missing_media_asset_ids = frozenset({"stale-asset"})
    by_asset = await admin_client.patch(
        f"/api/admin/questions/{created['id']}",
        json={**MC_BODY, "media_asset_id": "stale-asset"},
    )
    assert by_asset.status_code == 404
    assert by_asset.json()["code"] == "not_found"
