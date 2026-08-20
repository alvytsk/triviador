"""§10.2's list and editor, read half."""

from typing import Annotated

from fastapi import APIRouter, Query

from triviador.api.deps import AdminPrincipal, Deps
from triviador.api.errors import ApiError, ApiErrorCode
from triviador.api.schemas.admin.questions import (
    ChoiceView,
    QuestionDetail,
    QuestionPageView,
    QuestionSummary,
)
from triviador.domain.questions.types import Difficulty, QuestionKind
from triviador.services.admin import QuestionDetailRecord, QuestionFilters, QuestionSummaryRecord

router = APIRouter(prefix="/questions", tags=["admin"])

MAX_LIMIT = 200


def _summary(record: QuestionSummaryRecord) -> QuestionSummary:
    return QuestionSummary(
        id=record.question_id,
        kind=QuestionKind(record.kind),
        prompt=record.prompt,
        category_id=record.category_id,
        category_slug=record.category_slug,
        difficulty=Difficulty(record.difficulty),
        is_active=record.is_active,
        has_media=record.has_media,
        version=record.version,
        updated_at=record.updated_at,
    )


def detail(record: QuestionDetailRecord) -> QuestionDetail:
    """Shared with the write routes (Task 5), which answer with the same
    shape they read — a client that has to re-fetch after a save is a
    client that renders a stale form for one frame."""
    return QuestionDetail(
        id=record.question_id,
        kind=QuestionKind(record.kind),
        prompt=record.prompt,
        category_id=record.category_id,
        category_slug=record.category_slug,
        difficulty=Difficulty(record.difficulty),
        is_active=record.is_active,
        version=record.version,
        media_asset_id=record.media_asset_id,
        choices=(
            [
                ChoiceView(
                    idx=c.idx, text=c.text, is_correct=c.is_correct, media_asset_id=c.media_asset_id
                )
                for c in record.choices
            ]
            if record.choices is not None
            else None
        ),
        numeric_answer=record.numeric_answer,
        unit=record.unit,
    )


@router.get("")
async def list_questions(
    deps: Deps,
    principal: AdminPrincipal,
    kind: QuestionKind | None = None,
    category_id: str | None = None,
    difficulty: Difficulty | None = None,
    is_active: bool | None = None,
    has_media: bool | None = None,
    q: Annotated[str | None, Query(max_length=200)] = None,
    limit: Annotated[int, Query(ge=1, le=MAX_LIMIT)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> QuestionPageView:
    page = await deps.questions_admin.list(
        QuestionFilters(
            kind=None if kind is None else kind.value,
            category_id=category_id,
            difficulty=None if difficulty is None else difficulty.value,
            is_active=is_active,
            has_media=has_media,
            search=q,
        ),
        limit=limit,
        offset=offset,
    )
    return QuestionPageView(
        items=[_summary(item) for item in page.items],
        total=page.total,
        limit=limit,
        offset=offset,
    )


@router.get("/{question_id}")
async def get_question(question_id: str, deps: Deps, principal: AdminPrincipal) -> QuestionDetail:
    record = await deps.questions_admin.get(question_id)
    if record is None:
        raise ApiError(ApiErrorCode.NOT_FOUND, 404, "no such question")
    return detail(record)
