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

  it.each(['transform="translate(5,5)"', 'href="#x"', 'style="fill:red"', 'onclick="x()"'])(
    "rejects the disallowed attribute %s",
    (attr) => {
      expect(() =>
        parseMapSvg(svg(`<path id="a" d="M0 0z" ${attr}/><path id="b" d="M1 1z"/>`), IDS),
      ).toThrow(/disallowed attribute/);
    },
  );

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

  describe("the drift guard — four inputs a code review found the two validators disagreeing on", () => {
    // Same four documents, byte-for-byte, as backend/tests/maps/test_svg_validator.py's
    // "the drift guard" block. Each one used to get a different verdict from the two
    // sides; asserting both here and there means the next drift fails a test instead
    // of waiting for someone's browser.

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
      expect(() => parseMapSvg(doc, IDS)).toThrow(/not an SVG/);
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
