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

const warmupTurn = {
  kind: "media_warmup" as const,
  deadline_at: deadline(),
  deadline_id: 10,
  your_options: { pick: [], attack: [] },
};

// Default gameState() seats u1 (Alexey) / u2 (Petra) / u3 (Tomáš). u1 is
// attacker and defender-not, so flipping attacker_id/defender_id is what
// puts "you" on each side; a third id who is neither is the bystander.
const duelTurn = {
  kind: "battle_duel" as const,
  attacker_id: "u1",
  defender_id: "u2",
  region_id: "praha",
  tiebreak: false,
  question: question(),
  answered: [],
  your_answer: null,
  deadline_at: deadline(),
  deadline_id: 11,
  your_options: { pick: [], attack: [] },
};

const neutralTurn = {
  kind: "neutral_challenge" as const,
  attacker_id: "u1",
  region_id: "praha",
  question: question(),
  answered: [],
  your_answer: null,
  deadline_at: deadline(),
  deadline_id: 12,
  your_options: { pick: [], attack: [] },
};

const finalTurn = {
  kind: "final_tiebreak" as const,
  contenders: ["u1", "u3"],
  question: question(),
  answered: [],
  your_answer: null,
  deadline_at: deadline(),
  deadline_id: 13,
  your_options: { pick: [], attack: [] },
};

describe("game selectors", () => {
  it("finds you by state.you.player_id, not by comparing against /me", () => {
    // you.player_id points at u2 ("Petra"), who is *not* players[0] — a
    // youPlayer() that accidentally returned state.players[0] would still
    // report "Alexey" and this assertion would catch it.
    const state = gameState({ you: { player_id: "u2", role: "player" } });
    expect(youPlayer(state)?.display_name).toBe("Petra");
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

  it("says a spectating admin is never mid-question, even one still open", () => {
    const state = gameState({ you: { player_id: null, role: "admin" }, turn: questionTurn });
    expect(isYourTurn(state)).toBe(false);
  });

  it("says warmup is nobody's turn — there is nothing to do", () => {
    expect(isYourTurn(gameState({ turn: warmupTurn }))).toBe(false);
  });

  it("says the attacker may answer the duel", () => {
    expect(isYourTurn(gameState({ turn: duelTurn }))).toBe(true);
  });

  it("says the defender may answer the duel", () => {
    const asDefender = { ...duelTurn, attacker_id: "u2", defender_id: "u1" };
    expect(isYourTurn(gameState({ turn: asDefender }))).toBe(true);
  });

  it("says a seated bystander may not answer someone else's duel", () => {
    // This is the regression case for the Critical: your_options is empty
    // for every question-bearing turn, so a viewer merely being seated must
    // not be enough — only the two named participants may act.
    const bystanderDuel = { ...duelTurn, attacker_id: "u2", defender_id: "u3" };
    expect(isYourTurn(gameState({ turn: bystanderDuel }))).toBe(false);
  });

  it("stops treating a duel as yours once you have answered", () => {
    const answered = {
      ...duelTurn,
      your_answer: { kind: "choice" as const, idx: 0, value: null },
    };
    expect(isYourTurn(gameState({ turn: answered }))).toBe(false);
  });

  it("says the attacker may answer a neutral challenge", () => {
    expect(isYourTurn(gameState({ turn: neutralTurn }))).toBe(true);
  });

  it("says a non-attacker may not answer a neutral challenge", () => {
    const notAttacker = { ...neutralTurn, attacker_id: "u2" };
    expect(isYourTurn(gameState({ turn: notAttacker }))).toBe(false);
  });

  it("says a contender may answer the final tiebreak", () => {
    expect(isYourTurn(gameState({ turn: finalTurn }))).toBe(true);
  });

  it("says a non-contender may not answer the final tiebreak", () => {
    const notContender = { ...finalTurn, contenders: ["u2", "u3"] };
    expect(isYourTurn(gameState({ turn: notContender }))).toBe(false);
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
