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
    [
        'transform="translate(5,5)"',
        'href="#x"',
        'style="fill:red"',
        'onclick="x()"',
        # The whitelist rejects this by omission, same as the other three —
        # no special case was added for it, and round 4 found the omission
        # had a gap next to it (the drift guard's case A below), not in
        # this list.
        'xlink:href="#x" xmlns:xlink="http://www.w3.org/1999/xlink"',
    ],
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


GOOD_PATHS = '<path id="a" d="M0 0h1v1z"/><path id="b" d="M2 2h1v1z"/>'


def test_xmlns_xlink_on_the_root_is_accepted() -> None:
    """A namespace declaration, not content. ElementTree folds it out of
    `root.attrib` entirely (see `ROOT_ATTRS`'s comment); this asserts the
    outcome that follows from that, not the mechanism."""
    doc = (
        '<svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink" '
        f'viewBox="0 0 100 100">{GOOD_PATHS}</svg>'
    )
    assert validate_svg(doc, REGIONS) == ()


def test_a_root_with_no_xmlns_at_all_is_rejected() -> None:
    """An `<svg>` with no `xmlns` does not render as SVG in a browser, so
    accepting it here would be the lenient-and-wrong side of a disagreement
    with the DOM, which always requires the namespace to resolve."""
    doc = f'<svg viewBox="0 0 100 100">{GOOD_PATHS}</svg>'
    problems = validate_svg(doc, REGIONS)
    assert any("not an SVG" in p for p in problems)


def test_an_empty_viewbox_is_rejected() -> None:
    """Present but unusable — distinct from `test_missing_viewbox_is_a_problem`,
    which never sets the attribute at all."""
    doc = f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="">{GOOD_PATHS}</svg>'
    problems = validate_svg(doc, REGIONS)
    assert any("viewBox" in p for p in problems)


def test_a_wrong_root_reports_every_other_problem_too() -> None:
    """The drift guard: this input, and the three above, are the exact four
    documents `frontend/src/entities/map/model/parse.test.ts`'s "drift
    guard" block asserts against `parseMapSvg`. A code review found the two
    validators disagreeing on all four; asserting the same outcome in both
    suites means the next disagreement fails a test instead of shipping.

    Four problems, not one: wrong root, empty viewBox, the disallowed
    "bogus" attribute, and both regions missing (reported as one joined
    item). No `<path>` at all means nothing here depends on how a `<path>`
    child is scanned — only that scanning a wrong root doesn't stop after
    the first thing wrong with it, which is what `validate_svg` has always
    done and what the TypeScript side used to not do.
    """
    doc = '<html viewBox="" bogus="1"></html>'
    problems = validate_svg(doc, REGIONS)
    assert len(problems) == 4


def test_an_unnamespaced_path_is_rejected() -> None:
    """The fifth drift, found by a re-review hunting for a sibling of the
    root-namespace bug fixed above: `xmlns=""` on a `<path>` un-namespaces
    it under XML's own rule for an empty default-namespace declaration, and
    a browser drops it from the SVG rendering tree exactly as it would an
    unrecognised tag. `validate_svg`'s child check used the same lenient
    `child_ns not in (SVG_NS, "")` pattern the root check was tightened away
    from in the previous round — same bug, one function down, missed
    because the instruction named the root and not its sibling. TypeScript's
    child check was never lenient here, so this is a one-sided fix, not a
    shared test of a shared change."""
    doc = (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100">'
        '<path xmlns="" id="a" d="M0 0h1v1z"/><path id="b" d="M2 2h1v1z"/>'
        "</svg>"
    )
    problems = validate_svg(doc, REGIONS)
    assert any("is not allowed" in p for p in problems)


def test_an_xmlns_redeclaration_on_a_path_is_accepted() -> None:
    """Round 4's divergence A, found by a re-review that paired every
    conditional branch in this module against every branch in
    `frontend/src/entities/map/model/parse.ts` looking for one with no
    counterpart. `ElementTree` folds a namespace declaration out of
    `element.attrib` for *every* element, not just the root — so a `<path>`
    that redeclares its own namespace has always been fine here. The
    TypeScript side's root loop got an `xmlns`/`xmlns:*` skip in round 4's
    first fix; its path-attribute loop did not, until this round. Real SVG
    editors do emit per-element `xmlns` redeclarations, so ordinary tool
    output reached this."""
    doc = (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100">'
        '<path xmlns="http://www.w3.org/2000/svg" id="a" d="M0 0h1v1z"/>'
        '<path id="b" d="M2 2h1v1z"/>'
        "</svg>"
    )
    assert validate_svg(doc, REGIONS) == ()


def test_a_doctype_mentioned_only_in_a_comment_is_not_rejected() -> None:
    """Round 4's divergence B. `defusedxml`'s `forbid_dtd` rejects
    structurally, during the parse — it never even sees a comment's text as
    a DOCTYPE. The TypeScript side used to reject on a raw `/<!DOCTYPE/i`
    regex over the whole source, run *before* parsing, so a comment merely
    mentioning that text short-circuited every other check there. This
    asserts the correct, shared answer: a DOCTYPE only inside a comment is
    not a DOCTYPE."""
    doc = (
        "<!-- <!DOCTYPE fake> -->"
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100">'
        '<path id="a" d="M0 0h1v1z"/><path id="b" d="M2 2h1v1z"/></svg>'
    )
    assert validate_svg(doc, REGIONS) == ()


def test_a_non_flat_path_reports_one_message_per_child() -> None:
    """Round 4's cosmetic finding, item C. Not an accept/reject drift —
    both sides already rejected a `<path>` with children — but this
    function has always emitted one message per descendant here, while the
    TypeScript side emitted one message total, which would have lied to
    anyone using a problem count as evidence of which branch fired (as one
    round of review already did, on the no-xmlns case). Two children, two
    messages, each naming its tag."""
    doc = (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100">'
        '<path id="a" d="M0 0z"><rect/><circle/></path>'
        '<path id="b" d="M1 1z"/></svg>'
    )
    assert validate_svg(doc, REGIONS) == (
        "<path> has a child <rect>; the file must be flat",
        "<path> has a child <circle>; the file must be flat",
    )


def test_the_shipped_czechia_map_satisfies_the_contract() -> None:
    """The build-time half of §8.1. This is the gate that makes "a map is a
    two-file drop" safe: the drop is checked here, in the repository, rather
    than discovered by a player looking at a blank board."""
    source = json.loads((REPO_MAPS / "czechia" / "map.json").read_text(encoding="utf-8"))
    regions = [RegionId(r["id"]) for r in source["regions"]]
    assert validate_map_directory(REPO_MAPS, "czechia", regions) == ()


def test_a_map_directory_without_an_svg_is_reported() -> None:
    assert validate_map_directory(REPO_MAPS, "no-such-map", REGIONS) != ()
