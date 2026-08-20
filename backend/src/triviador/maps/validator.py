"""Spec 1B §8.1's SVG contract, enforced against the file.

The frontend enforces the same contract on the bytes it fetches
(`entities/map`, Task 11), because `map.svg` is dropped into a directory
rather than passed through a build. This module is the half that runs
against the repository, so a map that could never render is a red test
instead of a blank board.

**Why the whitelist is a whitelist.** `href`, `xlink:href`, `transform`,
`style` and `onclick` are all rejected by *not appearing* in `PATH_ATTRS`
rather than by being enumerated as forbidden. That is the only form of this
rule that stays correct as SVG grows a new attribute.

**Why a group is rejected structurally.** §8.1 accepts exactly one
transform contract — flattened, top-level paths — because supporting
"top-level paths *or* composed ancestors" would mean two transform engines,
this one's and the browser's, with room to disagree. So nesting is rejected
whether or not the wrapper carries a transform: the property that has to
hold is that there is nothing to disagree about.
"""

from collections.abc import Collection
from pathlib import Path
from xml.etree.ElementTree import Element

from defusedxml.common import DefusedXmlException
from defusedxml.ElementTree import ParseError, fromstring

from triviador.domain.ids import RegionId

SVG_NS = "http://www.w3.org/2000/svg"

PATH_ATTRS = frozenset({"id", "d", "fill-rule", "clip-rule"})
# `xmlns` never appears here: ElementTree folds namespace declarations into
# the tag name rather than reporting them as attributes.
ROOT_ATTRS = frozenset({"viewBox", "width", "height"})


def _split(tag: object) -> tuple[str, str]:
    """`{ns}local` → `(ns, local)`. A comment or PI has a callable tag."""
    if not isinstance(tag, str):
        return ("", "<non-element>")
    if tag.startswith("{"):
        namespace, _, local = tag[1:].partition("}")
        return (namespace, local)
    return ("", tag)


def validate_svg(source: str, region_ids: Collection[RegionId]) -> tuple[str, ...]:
    """Every problem, not the first one. An operator fixing a map wants the
    whole list, the same way `startup_problems` hands a deployment all of
    its misconfigurations at once."""
    try:
        root = fromstring(source, forbid_dtd=True, forbid_entities=True, forbid_external=True)
    except DefusedXmlException as exc:
        return (f"refused by the hardened parser: {exc}",)
    except ParseError as exc:
        return (f"not parseable as XML: {exc}",)

    problems: list[str] = []
    namespace, local = _split(root.tag)
    if local != "svg" or namespace not in (SVG_NS, ""):
        problems.append(f"root element is <{local}> in namespace {namespace!r}, not an SVG <svg>")
    if "viewBox" not in root.attrib:
        problems.append("root <svg> has no viewBox")
    for name in sorted(set(root.attrib) - ROOT_ATTRS):
        problems.append(f"root <svg> carries a disallowed attribute: {name}")

    seen: list[str] = []
    for child in root:
        child_ns, child_tag = _split(child.tag)
        if child_tag != "path" or child_ns not in (SVG_NS, ""):
            problems.append(f"<{child_tag}> is not allowed: every region is a top-level <path>")
            continue
        problems.extend(_path_problems(child, seen))
        for descendant in child:
            deep = _split(descendant.tag)[1]
            problems.append(f"<path> has a child <{deep}>; the file must be flat")

    duplicates = sorted({i for i in seen if seen.count(i) > 1})
    if duplicates:
        problems.append(f"duplicate path ids: {duplicates}")

    wanted = {str(r) for r in region_ids}
    got = set(seen)
    if missing := sorted(wanted - got):
        problems.append(f"regions in map.json with no path: {missing}")
    if extra := sorted(got - wanted):
        problems.append(f"paths with no region in map.json: {extra}")

    return tuple(problems)


def _path_problems(element: Element, seen: list[str]) -> list[str]:
    problems: list[str] = []
    for name in sorted(set(element.attrib) - PATH_ATTRS):
        problems.append(f"<path> carries a disallowed attribute: {name}")
    identifier = element.attrib.get("id")
    if identifier is None:
        problems.append("a <path> has no id")
        return problems
    if not element.attrib.get("d"):
        problems.append(f"path {identifier!r} has no d")
    seen.append(identifier)
    return problems


def validate_map_directory(
    root: Path, map_id: str, region_ids: Collection[RegionId]
) -> tuple[str, ...]:
    path = root / map_id / "map.svg"
    if not path.is_file():
        return (f"no map.svg at {path}",)
    return validate_svg(path.read_text(encoding="utf-8"), region_ids)
