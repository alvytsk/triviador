"""Spec 1B §8.1's contract, one test per line of it.

`_svg` builds a document that passes, so each test can break exactly one
rule and assert on that rule alone. A test that constructs its own whole
document per case drifts: the "rejects transform" test ends up also
missing a viewBox, passes for the wrong reason, and keeps passing after
the transform check is deleted.
"""

import json
from pathlib import Path

import pytest

from triviador.domain.ids import RegionId
from triviador.maps.validator import validate_map_directory, validate_svg

REPO_MAPS = Path(__file__).resolve().parents[3] / "data" / "maps"
REGIONS = (RegionId("a"), RegionId("b"))


def _svg(paths: str = "", root_attrs: str = 'viewBox="0 0 100 100"') -> str:
    body = paths or '<path id="a" d="M0 0h1v1z"/><path id="b" d="M2 2h1v1z"/>'
    return f'<svg xmlns="http://www.w3.org/2000/svg" {root_attrs}>{body}</svg>'


def test_a_flat_two_path_document_is_valid() -> None:
    assert validate_svg(_svg(), REGIONS) == ()


def test_missing_viewbox_is_a_problem() -> None:
    problems = validate_svg(_svg(root_attrs=""), REGIONS)
    assert any("viewBox" in p for p in problems)


@pytest.mark.parametrize(
    "element",
    [
        "<script>alert(1)</script>",
        "<foreignObject><div/></foreignObject>",
        '<use href="#a"/>',
        '<image href="x.png"/>',
        "<style>path{fill:red}</style>",
    ],
)
def test_forbidden_elements_are_rejected(element: str) -> None:
    doc = _svg(paths=f'<path id="a" d="M0 0h1v1z"/><path id="b" d="M2 2h1v1z"/>{element}')
    assert validate_svg(doc, REGIONS) != ()


def test_a_group_wrapper_is_rejected_even_without_a_transform() -> None:
    """§8.1 accepts one transform contract. A group is rejected structurally
    rather than only when it carries a transform, because the property that
    has to hold is "the browser and the validator cannot disagree", and a
    group is where they could."""
    doc = _svg(paths='<g><path id="a" d="M0 0h1v1z"/><path id="b" d="M2 2h1v1z"/></g>')
    assert validate_svg(doc, REGIONS) != ()


@pytest.mark.parametrize(
    "attr",
    ['transform="translate(5,5)"', 'href="#x"', 'style="fill:red"', 'onclick="x()"'],
)
def test_disallowed_path_attributes_are_rejected(attr: str) -> None:
    doc = _svg(paths=f'<path id="a" d="M0 0h1v1z" {attr}/><path id="b" d="M2 2h1v1z"/>')
    problems = validate_svg(doc, REGIONS)
    assert any("disallowed attribute" in p for p in problems)


def test_fill_rule_and_clip_rule_are_allowed() -> None:
    doc = _svg(
        paths=(
            '<path id="a" d="M0 0h1v1z" fill-rule="evenodd" clip-rule="evenodd"/>'
            '<path id="b" d="M2 2h1v1z"/>'
        )
    )
    assert validate_svg(doc, REGIONS) == ()


def test_a_path_missing_from_map_json_is_reported() -> None:
    doc = _svg(
        paths=(
            '<path id="a" d="M0 0h1v1z"/><path id="b" d="M2 2h1v1z"/><path id="c" d="M4 4h1v1z"/>'
        )
    )
    problems = validate_svg(doc, REGIONS)
    assert any("no region in map.json" in p for p in problems)


def test_a_region_with_no_path_is_reported() -> None:
    problems = validate_svg(_svg(paths='<path id="a" d="M0 0h1v1z"/>'), REGIONS)
    assert any("no path" in p for p in problems)


def test_duplicate_ids_are_reported() -> None:
    doc = _svg(
        paths=(
            '<path id="a" d="M0 0h1v1z"/><path id="a" d="M2 2h1v1z"/><path id="b" d="M4 4h1v1z"/>'
        )
    )
    problems = validate_svg(doc, REGIONS)
    assert any("duplicate" in p for p in problems)


def test_a_doctype_is_refused_by_the_parser() -> None:
    doc = '<!DOCTYPE svg><svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1 1"/>'
    assert validate_svg(doc, REGIONS) != ()


def test_an_entity_declaration_is_refused_by_the_parser() -> None:
    doc = (
        '<!DOCTYPE svg [<!ENTITY x "boom">]>'
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1 1">'
        '<path id="a" d="&x;"/></svg>'
    )
    assert validate_svg(doc, REGIONS) != ()


def test_unparseable_input_is_a_problem_not_an_exception() -> None:
    assert validate_svg("not xml at all", REGIONS) != ()


def test_the_shipped_czechia_map_satisfies_the_contract() -> None:
    """The build-time half of §8.1. This is the gate that makes "a map is a
    two-file drop" safe: the drop is checked here, in the repository, rather
    than discovered by a player looking at a blank board."""
    source = json.loads((REPO_MAPS / "czechia" / "map.json").read_text(encoding="utf-8"))
    regions = [RegionId(r["id"]) for r in source["regions"]]
    assert validate_map_directory(REPO_MAPS, "czechia", regions) == ()


def test_a_map_directory_without_an_svg_is_reported() -> None:
    assert validate_map_directory(REPO_MAPS, "no-such-map", REGIONS) != ()
