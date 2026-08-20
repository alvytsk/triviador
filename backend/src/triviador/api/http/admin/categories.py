from fastapi import APIRouter

from triviador.api.deps import AdminPrincipal, Deps
from triviador.api.errors import ApiError, ApiErrorCode
from triviador.api.schemas.admin.categories import (
    CategoryView,
    CreateCategoryRequest,
    RenameCategoryRequest,
)
from triviador.services.admin import CategoryRecord, SlugTaken

router = APIRouter(prefix="/categories", tags=["admin"])


def _view(record: CategoryRecord) -> CategoryView:
    return CategoryView(id=record.category_id, slug=record.slug, name=record.name)


@router.get("")
async def list_categories(deps: Deps, principal: AdminPrincipal) -> list[CategoryView]:
    return [_view(record) for record in await deps.categories.list()]


@router.post("", status_code=201)
async def create_category(
    body: CreateCategoryRequest, deps: Deps, principal: AdminPrincipal
) -> CategoryView:
    try:
        return _view(await deps.categories.create(slug=body.slug, name=body.name))
    except SlugTaken as exc:
        raise ApiError(
            ApiErrorCode.SLUG_TAKEN, 409, f"a category with slug {body.slug!r} already exists"
        ) from exc


@router.patch("/{category_id}")
async def rename_category(
    category_id: str, body: RenameCategoryRequest, deps: Deps, principal: AdminPrincipal
) -> CategoryView:
    record = await deps.categories.rename(category_id, name=body.name)
    if record is None:
        raise ApiError(ApiErrorCode.NOT_FOUND, 404, "no such category")
    return _view(record)
