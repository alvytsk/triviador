"""§10.6's CRUD, and §6.1's soft delete.

Editing a preset never touches a running game: `games.rules` holds a
frozen copy taken at creation (§6.2). The admin screen says so in a
sentence; this module is where that sentence is true.
"""

from dataclasses import asdict

from fastapi import APIRouter, Response

from triviador.api.deps import AdminPrincipal, Deps
from triviador.api.errors import ApiError, ApiErrorCode
from triviador.api.schemas.admin.presets import (
    PresetCoverage,
    PresetDetail,
    PresetWriteRequest,
)
from triviador.api.schemas.presets import RulesView
from triviador.domain.game.rules import GameRules, required_question_budget, validate_rules
from triviador.services.admin import DeactivateOutcome, PresetAdminRecord, UpdateOutcome

router = APIRouter(prefix="/presets", tags=["admin"])


def _detail(record: PresetAdminRecord) -> PresetDetail:
    return PresetDetail(
        id=record.preset_id,
        name=record.name,
        is_default=record.is_default,
        is_active=record.is_active,
        rules=RulesView(**asdict(record.rules)),
    )


def _rules(view: RulesView) -> GameRules:
    """`validate_rules` is the single definition of a legal ruleset
    (Plan 2). Restating its bounds in a Pydantic model would be a second
    copy, and the copy is the one that would drift."""
    rules = GameRules(**{**view.model_dump(), "claims_by_rank": tuple(view.claims_by_rank)})
    problems = validate_rules(rules)
    if problems:
        raise ApiError(ApiErrorCode.VALIDATION_FAILED, 422, "; ".join(problems))
    return rules


@router.get("")
async def list_presets(deps: Deps, principal: AdminPrincipal) -> list[PresetDetail]:
    return [_detail(record) for record in await deps.presets_admin.list_all()]


@router.post("", status_code=201)
async def create_preset(
    body: PresetWriteRequest, deps: Deps, principal: AdminPrincipal
) -> PresetDetail:
    record = await deps.presets_admin.create(
        name=body.name, rules=_rules(body.rules), is_default=body.is_default
    )
    return _detail(record)


@router.get("/{preset_id}")
async def get_preset(preset_id: str, deps: Deps, principal: AdminPrincipal) -> PresetDetail:
    record = await deps.presets_admin.get(preset_id)
    if record is None:
        raise ApiError(ApiErrorCode.NOT_FOUND, 404, "no such preset")
    return _detail(record)


@router.patch("/{preset_id}")
async def update_preset(
    preset_id: str, body: PresetWriteRequest, deps: Deps, principal: AdminPrincipal
) -> PresetDetail:
    outcome, record = await deps.presets_admin.update(
        preset_id, name=body.name, rules=_rules(body.rules), is_default=body.is_default
    )
    if outcome is UpdateOutcome.NOT_FOUND:
        raise ApiError(ApiErrorCode.NOT_FOUND, 404, "no such preset")
    if outcome is UpdateOutcome.WOULD_LEAVE_NO_DEFAULT:
        raise ApiError(
            ApiErrorCode.DEFAULT_PRESET,
            409,
            "this is the default preset; make another one default instead of clearing this one",
        )
    if outcome is UpdateOutcome.RETIRED_CANNOT_BE_DEFAULT:
        raise ApiError(
            ApiErrorCode.DEFAULT_PRESET,
            409,
            "a retired preset cannot be the default; reactivate it first",
        )
    assert record is not None  # every other outcome carries one
    return _detail(record)


@router.delete("/{preset_id}", status_code=204, response_class=Response)
async def deactivate_preset(preset_id: str, deps: Deps, principal: AdminPrincipal) -> Response:
    outcome = await deps.presets_admin.deactivate(preset_id)
    if outcome is DeactivateOutcome.NOT_FOUND:
        raise ApiError(ApiErrorCode.NOT_FOUND, 404, "no such preset")
    if outcome is DeactivateOutcome.IS_DEFAULT:
        raise ApiError(
            ApiErrorCode.DEFAULT_PRESET,
            409,
            "this is the default preset; make another one default first",
        )
    return Response(status_code=204)


@router.get("/{preset_id}/coverage")
async def preset_coverage(
    preset_id: str, deps: Deps, principal: AdminPrincipal
) -> PresetCoverage:
    record = await deps.presets_admin.get(preset_id)
    if record is None:
        raise ApiError(ApiErrorCode.NOT_FOUND, 404, "no such preset")
    budget = required_question_budget(record.rules)
    bank = await deps.questions_admin.active_counts()
    required = {"numeric": budget.numeric, "multiple_choice": budget.multiple_choice}
    return PresetCoverage(
        required=required,
        bank=bank,
        sufficient=all(bank.get(kind, 0) >= need for kind, need in required.items()),
    )
