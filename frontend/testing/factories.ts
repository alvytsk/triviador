import type {
  ClientGameState,
  ClientPlayer,
  ClientQuestion,
  ClientRules,
  ClientTerritory,
  GameSnapshot,
} from "@/shared/api";

export const RULES: ClientRules = {
  player_count: 3,
  expansion_rounds: 4,
  battle_rounds: 4,
  base_hp: 3,
  answer_timeout_ms: 20_000,
  pick_timeout_ms: 15_000,
  warmup_ms: 5_000,
  claims_by_rank: [2, 1, 0],
  pts_base: 1000,
  pts_territory: 200,
  pts_conquered: 400,
  pts_defense: 100,
};

export function player(overrides: Partial<ClientPlayer> = {}): ClientPlayer {
  return {
    player_id: "u1",
    display_name: "Alexey",
    seat: 0,
    score: 1000,
    bonus_score: 0,
    base_region: "plzensky",
    is_eliminated: false,
    ...overrides,
  };
}

export function territory(overrides: Partial<ClientTerritory> = {}): ClientTerritory {
  return {
    region_id: "praha",
    owner_id: null,
    kind: "normal",
    base_owner_id: null,
    base_hp: null,
    acquisition: null,
    ...overrides,
  };
}

export function question(overrides: Partial<ClientQuestion> = {}): ClientQuestion {
  return {
    question_id: "q1",
    kind: "multiple_choice",
    prompt: "Which river flows through Prague?",
    category: "Geography",
    difficulty: "easy",
    media_url: null,
    unit: null,
    choices: [
      { idx: 0, text: "Vltava", media_url: null },
      { idx: 1, text: "Labe", media_url: null },
      { idx: 2, text: "Morava", media_url: null },
      { idx: 3, text: "Odra", media_url: null },
    ],
    ...overrides,
  };
}

export function gameState(overrides: Partial<ClientGameState> = {}): ClientGameState {
  return {
    game_id: "g1",
    map_id: "czechia",
    phase: "expansion",
    round_no: 2,
    rules: RULES,
    players: [
      player(),
      player({ player_id: "u2", display_name: "Petra", seat: 1, base_region: "kralovehradecky" }),
      player({ player_id: "u3", display_name: "Tomáš", seat: 2, base_region: "jihomoravsky" }),
    ],
    territories: [territory()],
    turn: null,
    turn_order: ["u1", "u2", "u3"],
    winner_id: null,
    media_prefetch: [],
    you: { player_id: "u1", role: "player" },
    ...overrides,
  };
}

export function snapshot(seq: number, state: Partial<ClientGameState> = {}): GameSnapshot {
  return { seq, state: gameState(state) };
}

/** A deadline far enough out that a timer test never accidentally expires. */
export function deadline(msFromNow = 20_000): string {
  return new Date(Date.now() + msFromNow).toISOString();
}
