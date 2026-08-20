import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";
import { MapContractError, parseMapSvg } from "./parse";

const IDS = ["a", "b"];
const GOOD = '<path id="a" d="M0 0h1v1z"/><path id="b" d="M2 2h1v1z"/>';
const svg = (body = GOOD, attrs = 'viewBox="0 0 100 100"') =>
  `<svg xmlns="http://www.w3.org/2000/svg" ${attrs}>${body}</svg>`;

describe("parseMapSvg", () => {
  it("returns the viewBox and one region per path", () => {
    const parsed = parseMapSvg(svg(), IDS);
    expect(parsed.viewBox).toBe("0 0 100 100");
    expect(parsed.regions.map((r) => r.id)).toEqual(["a", "b"]);
    expect(parsed.regions[0]?.d).toBe("M0 0h1v1z");
  });

  it("keeps fill-rule and clip-rule, the only two style attributes allowed", () => {
    const parsed = parseMapSvg(
      svg(
        '<path id="a" d="M0 0z" fill-rule="evenodd" clip-rule="evenodd"/><path id="b" d="M1 1z"/>',
      ),
      IDS,
    );
    expect(parsed.regions[0]?.fillRule).toBe("evenodd");
  });

  it.each([
    ["a script", `${GOOD}<script>alert(1)</script>`],
    ["a foreignObject", `${GOOD}<foreignObject><div/></foreignObject>`],
    ["a use", `${GOOD}<use href="#a"/>`],
    ["an image", `${GOOD}<image href="x.png"/>`],
    ["a style element", `${GOOD}<style>path{fill:red}</style>`],
    ["a group wrapper", `<g>${GOOD}</g>`],
  ])("rejects %s", (_name, body) => {
    expect(() => parseMapSvg(svg(body), IDS)).toThrow(MapContractError);
  });

  it.each([
    'transform="translate(5,5)"',
    'href="#x"',
    'style="fill:red"',
    'onclick="x()"',
    // The whitelist rejects this by omission, same as the other three — no
    // special case was added for it, and round 4 found the omission had a
    // gap next to it (case A below), not in this list.
    'xlink:href="#x" xmlns:xlink="http://www.w3.org/1999/xlink"',
  ])("rejects the disallowed attribute %s", (attr) => {
    expect(() =>
      parseMapSvg(svg(`<path id="a" d="M0 0z" ${attr}/><path id="b" d="M1 1z"/>`), IDS),
    ).toThrow(/disallowed attribute/);
  });

  it("rejects a missing viewBox", () => {
    expect(() => parseMapSvg(svg(GOOD, ""), IDS)).toThrow(/viewBox/);
  });

  it("rejects an id that map.json does not know", () => {
    expect(() => parseMapSvg(svg(`${GOOD}<path id="c" d="M4 4z"/>`), IDS)).toThrow(/no region/);
  });

  it("rejects a region with no path — a hole in the board is not a board", () => {
    expect(() => parseMapSvg(svg('<path id="a" d="M0 0z"/>'), IDS)).toThrow(/no path/);
  });

  it("rejects duplicate ids", () => {
    expect(() =>
      parseMapSvg(
        svg('<path id="a" d="M0 0z"/><path id="a" d="M1 1z"/><path id="b" d="M2 2z"/>'),
        IDS,
      ),
    ).toThrow(/duplicate/);
  });

  it("rejects a DOCTYPE", () => {
    expect(() => parseMapSvg(`<!DOCTYPE svg>${svg()}`, IDS)).toThrow(MapContractError);
  });

  it("rejects something that is not XML at all", () => {
    expect(() => parseMapSvg("<html><body>404</body></html>", IDS)).toThrow(MapContractError);
  });

  it("reports every problem at once, not the first", () => {
    const bad = svg('<path id="a" d="M0 0z" transform="translate(1,1)"/>', "");
    const error = (() => {
      try {
        parseMapSvg(bad, IDS);
      } catch (e) {
        return e as Error;
      }
      throw new Error("expected a throw");
    })();
    expect(error.message).toMatch(/viewBox/);
    expect(error.message).toMatch(/disallowed attribute/);
    expect(error.message).toMatch(/no path/);
  });

  describe("the drift guard — eight inputs a code review found the two validators disagreeing on", () => {
    // Same eight documents, byte-for-byte, as backend/tests/maps/test_svg_validator.py's
    // "the drift guard" block. Cases 1-5 came from two rounds of input-probing; 6-8 came
    // from a round that instead paired every conditional branch in validator.py against
    // every branch in parse.ts, looking for a branch with no counterpart — a different
    // method that found two more divergences in one pass. Each case used to get a
    // different verdict from the two sides (case 8 is same-verdict but different-count,
    // see its own comment); asserting both here and there means the next drift fails a
    // test instead of waiting for someone's browser.

    it("accepts xmlns:xlink on the root — a namespace declaration, not content", () => {
      const doc =
        '<svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink" ' +
        'viewBox="0 0 100 100">' +
        GOOD +
        "</svg>";
      const parsed = parseMapSvg(doc, IDS);
      expect(parsed.regions).toHaveLength(2);
    });

    it("rejects a root with no xmlns at all — it would not render as SVG in a browser", () => {
      const doc = `<svg viewBox="0 0 100 100">${GOOD}</svg>`;
      let caught: MapContractError | null = null;
      try {
        parseMapSvg(doc, IDS);
      } catch (e) {
        caught = e as MapContractError;
      }
      expect(caught?.message).toMatch(/not an SVG/);
      // Both validators now reject the root *and* each un-namespaced child
      // (the fifth drift, below) rather than only the root, so the problem
      // counts agree too: wrong root + 2 "not allowed" paths + one joined
      // "regions with no path" — four on each side, not the 4-vs-1 split a
      // re-review flagged before the child check was tightened to match.
      expect(caught?.problems).toHaveLength(4);
    });

    it("rejects an empty viewBox — present but unusable", () => {
      const doc = `<svg xmlns="http://www.w3.org/2000/svg" viewBox="">${GOOD}</svg>`;
      expect(() => parseMapSvg(doc, IDS)).toThrow(/viewBox/);
    });

    it("reports all four problems on a wrong root, not just the first", () => {
      const doc = '<html viewBox="" bogus="1"></html>';
      let caught: MapContractError | null = null;
      try {
        parseMapSvg(doc, IDS);
      } catch (e) {
        caught = e as MapContractError;
      }
      expect(caught).toBeInstanceOf(MapContractError);
      // root is <html>, viewBox is empty, "bogus" is a disallowed attribute,
      // and both regions are missing (reported as one joined item) — four,
      // not the one this parser used to short-circuit to.
      expect(caught?.problems).toHaveLength(4);
    });

    it('rejects a <path xmlns=""> — the fifth drift, and the sibling of the fourth', () => {
      // A re-review was asked to hunt for a fifth disagreement rather than
      // confirm the fourth was fixed, and found this: `validator.py`'s root
      // check was tightened to require SVG_NS exactly, but its *child*
      // check kept the old `child_ns not in (SVG_NS, "")` leniency one
      // function below — the identical bug, missed because the instruction
      // named the root and not its sibling. `xmlns=""` un-namespaces a
      // `<path>` under XML's own rule for an empty default-namespace
      // declaration; a browser drops it from the SVG tree exactly as it
      // would an unrecognised tag. TypeScript's child check was never
      // lenient here (it always compared with strict equality to SVG_NS),
      // so this test is a one-sided guard: it exists to catch Python
      // drifting loose again, not because this file needed a fix too.
      const doc =
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100">' +
        '<path xmlns="" id="a" d="M0 0h1v1z"/><path id="b" d="M2 2h1v1z"/>' +
        "</svg>";
      expect(() => parseMapSvg(doc, IDS)).toThrow(/is not allowed/);
    });

    it("accepts an xmlns redeclaration on a <path> — round 4's divergence A", () => {
      // Round 1's fix skipped `xmlns`/`xmlns:*` in the *root* attribute
      // loop only. Python cannot see those attributes on a `<path>` either
      // — ElementTree folds namespace declarations out of `element.attrib`
      // for every element, not just the root — so a `<path>` that
      // redeclares its own namespace was fine there and wrongly rejected
      // here, because this file's path-attribute loop never got the same
      // skip. Real SVG editors do emit per-element `xmlns` redeclarations,
      // so ordinary tool output reached this. `isNamespaceDeclaration` is
      // now shared by both loops for exactly this reason.
      const doc =
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100">' +
        '<path xmlns="http://www.w3.org/2000/svg" id="a" d="M0 0h1v1z"/>' +
        '<path id="b" d="M2 2h1v1z"/>' +
        "</svg>";
      const parsed = parseMapSvg(doc, IDS);
      expect(parsed.regions).toHaveLength(2);
    });

    it("does not let a DOCTYPE mentioned only inside a comment decide the verdict — round 4's divergence B", () => {
      // This file used to reject on a raw `/<!DOCTYPE/i` regex over the
      // whole source, run *before* parsing — so a comment that merely
      // mentions the text "<!DOCTYPE" short-circuited every other check,
      // where Python's rejection (via `defusedxml`'s `forbid_dtd`) is
      // structural and never even sees this as a DOCTYPE. The check is now
      // `document.doctype !== null`, read after parsing, which is the
      // genuine equivalent.
      const doc =
        "<!-- <!DOCTYPE fake> -->" +
        `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100">${GOOD}</svg>`;
      const parsed = parseMapSvg(doc, IDS);
      expect(parsed.regions).toHaveLength(2);
    });

    it("reports one message per child of a non-flat <path>, naming each tag — round 4's cosmetic finding", () => {
      // Not an accept/reject drift — both sides already rejected a <path>
      // with children — but Python has always emitted one message per
      // descendant and this file emitted one generic message total, so a
      // problem count taken as evidence of which branch fired (as one
      // round of review already did, on the no-xmlns case) would have lied
      // here. Two children, two messages, each naming its tag.
      const doc = svg('<path id="a" d="M0 0z"><rect/><circle/></path><path id="b" d="M1 1z"/>');
      let caught: MapContractError | null = null;
      try {
        parseMapSvg(doc, IDS);
      } catch (e) {
        caught = e as MapContractError;
      }
      expect(caught?.problems).toEqual([
        "<path> has a child <rect>; the file must be flat",
        "<path> has a child <circle>; the file must be flat",
      ]);
    });
  });

  it("agrees with the Python validator about the shipped map", () => {
    // The claim §8.1 makes is that build time and run time enforce *the same*
    // contract. `backend/tests/maps/test_svg_validator.py` asserts this file
    // passes there; this asserts it passes here. If one of them ever fails
    // alone, the two implementations have drifted and one is wrong.
    const root = resolve(__dirname, "../../../../../data/maps/czechia");
    const map = JSON.parse(readFileSync(resolve(root, "map.json"), "utf8")) as {
      regions: { id: string }[];
    };
    const parsed = parseMapSvg(
      readFileSync(resolve(root, "map.svg"), "utf8"),
      map.regions.map((r) => r.id),
    );
    expect(parsed.regions).toHaveLength(map.regions.length);
    expect(parsed.viewBox).toBeTruthy();
  });
});
