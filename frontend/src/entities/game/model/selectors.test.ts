import { describe, expect, it } from "vitest";
import { deadline, gameState, question } from "../../../../testing/factories";
import {
  answeredBy,
  deadlineIdOf,
  deadlineOf,
  isYourTurn,
  youPlayer,
  yourAnswer,
  yourOptions,
} from "./selectors";

const pickingTurn = {
  kind: "expansion_picking" as const,
  current_picker: "u1",
  pick_order: ["u1", "u2", "u3"],
  grants_remaining: { u1: 2, u2: 1, u3: 0 },
  deadline_at: deadline(),
  deadline_id: 7,
  your_options: { pick: ["praha", "vysocina"], attack: [] },
};

const questionTurn = {
  kind: "expansion_question" as const,
  question: question(),
  answered: ["u3"],
  your_answer: null,
  deadline_at: deadline(),
  deadline_id: 9,
  your_options: { pick: [], attack: [] },
};

describe("game selectors", () => {
  it("finds you by state.you.player_id, not by comparing against /me", () => {
    const state = gameState();
    expect(youPlayer(state)?.display_name).toBe("Alexey");
  });

  it("returns null for a spectating admin who is in no seat", () => {
    const state = gameState({ you: { player_id: null, role: "admin" } });
    expect(youPlayer(state)).toBeNull();
  });

  it("says it is your turn when your_options offers something", () => {
    expect(isYourTurn(gameState({ turn: pickingTurn }))).toBe(true);
  });

  it("says it is not your turn when both option lists are empty", () => {
    const notYours = {
      ...pickingTurn,
      current_picker: "u2",
      your_options: { pick: [], attack: [] },
    };
    expect(isYourTurn(gameState({ turn: notYours }))).toBe(false);
  });

  it("treats an open question you have not answered as your turn", () => {
    expect(isYourTurn(gameState({ turn: questionTurn }))).toBe(true);
  });

  it("stops treating it as your turn once you have answered", () => {
    const answered = {
      ...questionTurn,
      your_answer: { kind: "choice" as const, idx: 1, value: null },
    };
    expect(isYourTurn(gameState({ turn: answered }))).toBe(false);
  });

  it("reads the deadline and its id off whichever turn is open", () => {
    const state = gameState({ turn: questionTurn });
    expect(deadlineOf(state)).toBe(questionTurn.deadline_at);
    expect(deadlineIdOf(state)).toBe(9);
  });

  it("has no deadline in a lobby", () => {
    expect(deadlineOf(gameState({ phase: "lobby", turn: null }))).toBeNull();
    expect(deadlineIdOf(gameState({ phase: "lobby", turn: null }))).toBeNull();
  });

  it("reports who has answered, and your own answer", () => {
    const state = gameState({ turn: questionTurn });
    expect(answeredBy(state)).toEqual(["u3"]);
    expect(yourAnswer(state)).toBeNull();
  });

  it("returns empty options rather than undefined for a turnless state", () => {
    expect(yourOptions(gameState({ turn: null }))).toEqual({ pick: [], attack: [] });
  });
});
