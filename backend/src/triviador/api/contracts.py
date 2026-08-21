"""§7's five documents.

`openapi.json` is documentation and a second drift signal.
`rest.schema.json` and `admin.schema.json` are what the generator actually
consumes, exported separately with `$defs` resolved for the reason §7
gives: an OpenAPI document's `$ref`s point at `#/components/schemas/...`,
which JSON Schema tooling cannot resolve. The two are split rather than
merged so a player who never opens `/admin` never pulls admin DTOs (and
their top-level Zod construction) into their bundle.
"""

import json
from pathlib import Path
from typing import Any

from pydantic import TypeAdapter
from pydantic.json_schema import models_json_schema

from triviador.api.errors import ApiErrorCode
from triviador.api.schemas.admin.categories import (
    CategoryView,
    CreateCategoryRequest,
    RenameCategoryRequest,
)
from triviador.api.schemas.admin.imports import ImportNotice, ImportRejection, ImportSummary
from triviador.api.schemas.admin.invites import InviteView, IssuedInvite, IssueInvitesRequest
from triviador.api.schemas.admin.media import MediaAssetSummary
from triviador.api.schemas.admin.presets import PresetCoverage, PresetDetail, PresetWriteRequest
from triviador.api.schemas.admin.questions import (
    QuestionDetail,
    QuestionPageView,
    QuestionSaved,
    QuestionSummary,
    QuestionWriteRequest,
)
from triviador.api.schemas.admin.users import SetRoleRequest, UserView
from triviador.api.schemas.auth import LoginRequest, Me, RedeemRequest
from triviador.api.schemas.errors import ErrorEnvelope
from triviador.api.schemas.games import CreateGameRequest, GameSnapshot, LobbyGameSummary
from triviador.api.schemas.maps import MapDetail, MapSummary
from triviador.api.schemas.presets import PresetSummary
from triviador.api.schemas.ws import ClientMessage, ServerMessage
from triviador.domain.game.actions import RejectCode

REST_MODELS = (
    RedeemRequest,
    LoginRequest,
    Me,
    CreateGameRequest,
    GameSnapshot,
    LobbyGameSummary,
    MapSummary,
    MapDetail,
    PresetSummary,
    ErrorEnvelope,
)

REF_TEMPLATE = "#/$defs/{model}"


def rest_schema() -> dict[str, Any]:
    _, schema = models_json_schema(
        [(model, "serialization") for model in REST_MODELS],
        ref_template=REF_TEMPLATE,
        title="TriviadorRest",
    )
    return schema


ADMIN_MODELS = (
    QuestionSummary,
    QuestionDetail,
    QuestionPageView,
    QuestionWriteRequest,
    QuestionSaved,
    CategoryView,
    CreateCategoryRequest,
    RenameCategoryRequest,
    MediaAssetSummary,
    ImportSummary,
    ImportRejection,
    ImportNotice,
    IssueInvitesRequest,
    IssuedInvite,
    InviteView,
    UserView,
    SetRoleRequest,
    PresetDetail,
    PresetWriteRequest,
    PresetCoverage,
)


def admin_schema() -> dict[str, Any]:
    """A separate document, not more `$defs` in `rest.schema.json`.

    §7's split is what keeps admin schemas out of the player bundle:
    `codegen.mjs` emits one module per document, and top-level Zod
    construction is a side effect no tree-shaker removes. A player who
    never opens `/admin` must never construct `QuestionWriteRequest`.
    """
    _, schema = models_json_schema(
        [(model, "serialization") for model in ADMIN_MODELS],
        ref_template=REF_TEMPLATE,
        title="TriviadorAdmin",
    )
    return schema


def ws_schema() -> dict[str, Any]:
    return {
        "title": "TriviadorWs",
        "$defs": {
            **TypeAdapter(ClientMessage).json_schema(ref_template=REF_TEMPLATE).get("$defs", {}),
            **TypeAdapter(ServerMessage).json_schema(ref_template=REF_TEMPLATE).get("$defs", {}),
        },
    }


def errors_schema() -> dict[str, Any]:
    return {
        "api_error_code": sorted(c.value for c in ApiErrorCode),
        "reject_code": sorted(c.value for c in RejectCode),
    }


def export_contracts(out_dir: Path) -> None:
    from triviador.api.app import create_app
    from triviador.api.deps import AppDependencies

    out_dir.mkdir(parents=True, exist_ok=True)
    # `app.openapi()` needs an app but not a database: `create_app` takes
    # its dependencies as an argument precisely so this is possible.
    app = create_app(AppDependencies.placeholder())
    documents = {
        "openapi.json": app.openapi(),
        "rest.schema.json": rest_schema(),
        "ws.schema.json": ws_schema(),
        "admin.schema.json": admin_schema(),
        "errors.json": errors_schema(),
    }
    for name, document in documents.items():
        # `sort_keys` and a trailing newline: the drift check is
        # `git diff --exit-code`, so byte-for-byte stability across Python
        # versions and dict orderings is the whole point.
        (out_dir / name).write_text(
            json.dumps(document, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
