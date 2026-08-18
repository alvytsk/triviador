"""§7's export. Four documents, each with a job.

`rest.schema.json` is separate from `openapi.json` because
`json-schema-to-zod` cannot consume an OpenAPI document's
`components.schemas` as though the document were JSON Schema — the `$ref`s
point at `#/components/schemas/...`, which is not a JSON Schema location.
"""

import json
from pathlib import Path
from typing import Any

import pytest

from triviador.api.contracts import export_contracts
from triviador.api.errors import ApiErrorCode
from triviador.domain.game.actions import RejectCode

JsonDocument = dict[str, Any]


@pytest.fixture
def contracts(tmp_path: Path) -> dict[str, JsonDocument]:
    export_contracts(tmp_path)
    return {p.name: json.loads(p.read_text()) for p in tmp_path.glob("*.json")}


def test_all_four_documents_are_written(contracts: dict[str, JsonDocument]) -> None:
    assert set(contracts) == {"openapi.json", "rest.schema.json", "ws.schema.json", "errors.json"}


def test_the_rest_schema_resolves_its_refs_locally(contracts: dict[str, JsonDocument]) -> None:
    """Every `$ref` must point inside `$defs`, or the generator emits a
    module full of `any`."""
    text = json.dumps(contracts["rest.schema.json"])
    assert "#/components/schemas/" not in text
    defs = contracts["rest.schema.json"]["$defs"]
    for ref in _refs(contracts["rest.schema.json"]):
        assert ref.startswith("#/$defs/") and ref.removeprefix("#/$defs/") in defs


def test_the_rest_schema_covers_every_player_facing_response(
    contracts: dict[str, JsonDocument],
) -> None:
    defs = contracts["rest.schema.json"]["$defs"]
    for name in ("GameSnapshot", "LobbyGameSummary", "MapDetail", "Me", "ErrorEnvelope"):
        assert name in defs


def test_the_ws_schema_carries_both_directions(contracts: dict[str, JsonDocument]) -> None:
    defs = contracts["ws.schema.json"]["$defs"]
    assert "SubmitAnswerFrame" in defs
    assert "UpdateMessage" in defs


def test_every_client_frame_forbids_extra_properties(contracts: dict[str, JsonDocument]) -> None:
    """§6.5's strictness has to survive the export, or the generated Zod
    objects are not `.strict()` and the guarantee stops at the backend."""
    defs = contracts["ws.schema.json"]["$defs"]
    for name in ("SubscribeFrame", "PingFrame", "SurrenderFrame", "SubmitAnswerFrame"):
        assert defs[name]["additionalProperties"] is False


def test_no_exported_schema_declares_an_actor(contracts: dict[str, JsonDocument]) -> None:
    for name, schema in contracts["ws.schema.json"]["$defs"].items():
        assert "actor_id" not in schema.get("properties", {}), name


def test_no_exported_schema_declares_an_answer_field(contracts: dict[str, JsonDocument]) -> None:
    """The structural guarantee, checked at the contract boundary: if the
    field is not in the schema, the generated TypeScript has no name for it
    and no client can read one."""
    forbidden = {"is_correct", "correct_value", "numeric_answer", "correct_choice_index"}
    for document in ("rest.schema.json", "ws.schema.json"):
        for name, schema in contracts[document]["$defs"].items():
            if name.startswith(("QuestionResolved", "RevealedAnswer")):
                continue  # the reveal, which is supposed to carry them
            assert not (set(schema.get("properties", {})) & forbidden), (document, name)


def test_errors_exports_both_enums_and_they_stay_disjoint(
    contracts: dict[str, JsonDocument],
) -> None:
    errors = contracts["errors.json"]
    assert set(errors["api_error_code"]) == {c.value for c in ApiErrorCode}
    assert set(errors["reject_code"]) == {c.value for c in RejectCode}
    assert not set(errors["api_error_code"]) & set(errors["reject_code"])


def _refs(node: object) -> list[str]:
    if isinstance(node, dict):
        found = [node["$ref"]] if "$ref" in node else []
        return found + [r for v in node.values() for r in _refs(v)]
    if isinstance(node, list):
        return [r for v in node for r in _refs(v)]
    return []
