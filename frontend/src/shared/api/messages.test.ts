import { describe, expectTypeOf, it } from "vitest";
import { question } from "../../../testing/factories";
import { type Turn, turnOf } from "./messages";

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
