import { describe, expect, expectTypeOf, it } from "vitest";
import { question } from "../../../testing/factories";
import { parseClientEvent, parseServerMessage, type Turn, turnOf } from "./messages";

/**
 * `Turn` exists precisely because `ClientGameState["turn"]` collapses to
 * `any` — the code generator cannot express the seven-kind `oneOf` as a Zod
 * discriminated union, so it falls back to `z.any().superRefine(...)`. A
 * runtime test cannot catch a regression back to `any`: every property
 * access on `any` succeeds at both compile time and runtime, so a broken
 * `Turn` would still pass every selector test in `entities/game` and
 * `entities/question`. Only a type-level assertion can.
 */
describe("Turn", () => {
  it("is a real union, not the `any` the generator collapses `state.turn` to", () => {
    expectTypeOf<Turn>().not.toBeAny();
    expectTypeOf(turnOf).returns.not.toBeAny();
  });

  it("narrows a question-bearing member so an unknown property is a type error", () => {
    const turn: Turn = {
      kind: "expansion_question",
      question: question(),
      answered: [],
      your_answer: null,
      deadline_at: new Date().toISOString(),
      deadline_id: 1,
      your_options: { pick: [], attack: [] },
    };

    if ("question" in turn) {
      expectTypeOf(turn.question).toEqualTypeOf(question());
      // @ts-expect-error - no member of `Turn` carries a `nonsense` field; this
      // only fails to error if `turn` has regressed to `any`.
      turn.nonsense;
    }
  });
});

/**
 * Three of `EVENT_SCHEMAS`' entries — `player_gone`, `round`, `turn_ended`
 * — have a `type` field that is a multi-value enum, not a single literal:
 * the wire value is never the family name. Each of the two real wire values
 * per family must be keyed to its schema (the way `lobbyMessageSchema` is
 * keyed under both `lobby.snapshot` and `lobby.update`) or that family can
 * never narrate.
 */
describe("parseClientEvent — multi-value event types", () => {
  it("parses player_eliminated", () => {
    const event = parseClientEvent({ type: "player_eliminated", player_id: "u1" });
    expect(event).not.toBeNull();
    expect(event?.type).toBe("player_eliminated");
  });

  it("parses player_surrendered", () => {
    const event = parseClientEvent({ type: "player_surrendered", player_id: "u1" });
    expect(event).not.toBeNull();
    expect(event?.type).toBe("player_surrendered");
  });

  it("parses round_started", () => {
    const event = parseClientEvent({ type: "round_started", phase: "expansion", round_no: 2 });
    expect(event).not.toBeNull();
    expect(event?.type).toBe("round_started");
  });

  it("parses round_completed", () => {
    const event = parseClientEvent({ type: "round_completed", phase: "battle", round_no: 3 });
    expect(event).not.toBeNull();
    expect(event?.type).toBe("round_completed");
  });

  it("parses turn_skipped", () => {
    const event = parseClientEvent({ type: "turn_skipped", attacker_id: "u1", reason: "timeout" });
    expect(event).not.toBeNull();
    expect(event?.type).toBe("turn_skipped");
  });

  it("parses turn_aborted", () => {
    const event = parseClientEvent({
      type: "turn_aborted",
      attacker_id: null,
      reason: "disconnect",
    });
    expect(event).not.toBeNull();
    expect(event?.type).toBe("turn_aborted");
  });
});

/**
 * `type in EVENT_SCHEMAS` / `type in SERVER_SCHEMAS` would walk the
 * prototype chain: a `type` of `"toString"` is `in` any plain object via
 * `Object.prototype`, so the naive lookup hands back a function with no
 * `.safeParse` and throws — breaking each function's documented promise
 * that an unrecognised type is dropped, not thrown, on input that is
 * untrusted (a socket frame, or a narration event inside one).
 */
describe("prototype-chain lookups don't throw", () => {
  it("parseClientEvent drops a type of toString rather than throwing", () => {
    expect(() => parseClientEvent({ type: "toString" })).not.toThrow();
    expect(parseClientEvent({ type: "toString" })).toBeNull();
  });

  it("parseServerMessage drops a type of toString rather than throwing", () => {
    const raw = JSON.stringify({ type: "toString" });
    expect(() => parseServerMessage(raw)).not.toThrow();
    expect(parseServerMessage(raw)).toBeNull();
  });
});
