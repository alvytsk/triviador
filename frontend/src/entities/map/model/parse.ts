const SVG_NS = "http://www.w3.org/2000/svg";
const PATH_ATTRS = new Set(["id", "d", "fill-rule", "clip-rule"]);
// `xmlns` and `xmlns:*` are not in this whitelist and never checked against
// it — they are namespace declarations, not content, and the loop below
// skips them structurally before the whitelist check ever runs. That mirrors
// the Python side, where ElementTree folds them out of `root.attrib`
// entirely rather than reporting them as attributes at all.
const ROOT_ATTRS = new Set(["viewBox", "width", "height"]);

export interface ParsedRegion {
  id: string;
  d: string;
  fillRule?: string;
  clipRule?: string;
}

export interface ParsedMap {
  viewBox: string;
  regions: readonly ParsedRegion[];
}

export class MapContractError extends Error {
  constructor(readonly problems: readonly string[]) {
    super(`map.svg does not satisfy the contract:\n- ${problems.join("\n- ")}`);
    this.name = "MapContractError";
  }
}

/**
 * §8.1's contract, enforced in the browser — defence in depth, because the
 * asset is *fetched*: it never passed through a build, and the file the
 * server has today is not necessarily the file the repository gated.
 *
 * `DOMParser` with `image/svg+xml` builds a detached document: nothing is
 * inserted, no script runs, no network fetch is triggered. What comes out is
 * a list of `d` strings that React renders as its own `<path>` elements
 * (§8.1: no `dangerouslySetInnerHTML`, so React keeps ownership of fills,
 * strokes and handlers).
 *
 * It fails closed and reports every problem, because the caller's only
 * sensible reaction is to show a named error rather than a partial board
 * (decision 12), and whoever fixes the map wants the whole list.
 */
export function parseMapSvg(source: string, regionIds: readonly string[]): ParsedMap {
  const problems: string[] = [];

  if (/<!DOCTYPE/i.test(source)) {
    // No DTD, no entities (§8.1). `DOMParser` will not expand external
    // entities, but a DOCTYPE has no legitimate reason to be in a normalized
    // map and refusing it is one line.
    throw new MapContractError(["the file carries a DOCTYPE"]);
  }

  const document = new DOMParser().parseFromString(source, "image/svg+xml");
  if (document.getElementsByTagName("parsererror").length > 0) {
    throw new MapContractError(["the file is not parseable as XML"]);
  }

  const root = document.documentElement;
  // Appended, not thrown: a wrong-but-parseable root (an HTML error page in
  // place of the SVG is the real-world version of this) still has a tree
  // worth scanning, and an operator wants every problem in one report, not
  // one line followed by a second run once this one is fixed. Only a
  // genuine parse failure above — where no tree exists at all — short-
  // circuits; this mirrors `validate_svg` on the Python side exactly.
  if (root.namespaceURI !== SVG_NS || root.localName !== "svg") {
    problems.push(`the root element is <${root.localName}>, not an SVG <svg>`);
  }

  const viewBox = root.getAttribute("viewBox");
  if (viewBox === null || viewBox.trim() === "") problems.push("the root <svg> has no viewBox");
  for (const attribute of Array.from(root.attributes)) {
    if (attribute.name === "xmlns" || attribute.name.startsWith("xmlns:")) continue;
    if (!ROOT_ATTRS.has(attribute.name)) {
      problems.push(`the root <svg> carries a disallowed attribute: ${attribute.name}`);
    }
  }

  const regions: ParsedRegion[] = [];
  const seen: string[] = [];
  for (const child of Array.from(root.children)) {
    if (child.namespaceURI !== SVG_NS || child.localName !== "path") {
      problems.push(`<${child.localName}> is not allowed: every region is a top-level <path>`);
      continue;
    }
    if (child.children.length > 0) {
      problems.push(`<path> has children; the file must be flat`);
    }
    for (const attribute of Array.from(child.attributes)) {
      if (!PATH_ATTRS.has(attribute.name)) {
        problems.push(`<path> carries a disallowed attribute: ${attribute.name}`);
      }
    }
    const id = child.getAttribute("id");
    const d = child.getAttribute("d");
    if (id === null) {
      problems.push("a <path> has no id");
      continue;
    }
    seen.push(id);
    if (d === null || d === "") {
      problems.push(`path "${id}" has no d`);
      continue;
    }
    const fillRule = child.getAttribute("fill-rule");
    const clipRule = child.getAttribute("clip-rule");
    regions.push({
      id,
      d,
      ...(fillRule === null ? {} : { fillRule }),
      ...(clipRule === null ? {} : { clipRule }),
    });
  }

  const duplicates = [...new Set(seen.filter((id, index) => seen.indexOf(id) !== index))].sort();
  if (duplicates.length > 0) problems.push(`duplicate path ids: ${duplicates.join(", ")}`);

  const wanted = new Set(regionIds);
  const got = new Set(seen);
  const missing = [...wanted].filter((id) => !got.has(id)).sort();
  const extra = [...got].filter((id) => !wanted.has(id)).sort();
  if (missing.length > 0) problems.push(`regions with no path: ${missing.join(", ")}`);
  if (extra.length > 0) problems.push(`paths with no region in map.json: ${extra.join(", ")}`);

  if (problems.length > 0) throw new MapContractError(problems);
  return { viewBox: viewBox as string, regions };
}
