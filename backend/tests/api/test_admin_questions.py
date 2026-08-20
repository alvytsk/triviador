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
