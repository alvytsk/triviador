import type { ClientGameState, ClientPlayer, SubmittedValue, YourOptions } from "@/shared/api";

const NO_OPTIONS: YourOptions = { pick: [], attack: [] };

/** Who you are *in this game*. §8.7: the projection carries `you` precisely
 *  so the client never correlates `/api/auth/me` against the player list and
 *  gets it wrong for a spectating admin. */
export function youPlayer(state: ClientGameState): ClientPlayer | null {
  const id = state.you.player_id;
  if (id === null) return null;
  return state.players.find((p) => p.player_id === id) ?? null;
}

export function playerById(state: ClientGameState, playerId: string): ClientPlayer | null {
  return state.players.find((p) => p.player_id === playerId) ?? null;
}

export function turnKindOf(state: ClientGameState): string | null {
  return state.turn === null ? null : state.turn.kind;
}

export function yourOptions(state: ClientGameState): YourOptions {
  return state.turn?.your_options ?? NO_OPTIONS;
}

export function deadlineOf(state: ClientGameState): string | null {
  return state.turn?.deadline_at ?? null;
}

export function deadlineIdOf(state: ClientGameState): number | null {
  return state.turn?.deadline_id ?? null;
}

export function answeredBy(state: ClientGameState): readonly string[] {
  const turn = state.turn;
  return turn !== null && "answered" in turn ? turn.answered : [];
}

export function yourAnswer(state: ClientGameState): SubmittedValue | null {
  const turn = state.turn;
  return turn !== null && "your_answer" in turn ? turn.your_answer : null;
}

/**
 * The one definition of "you can act right now", so no two screens disagree.
 *
 * It is derived entirely from the projection's affordances (§8.8) plus
 * whether you have already answered — never from comparing `current_picker`
 * to your id, and never from a rule. A viewer who is offered nothing is
 * watching, whatever the turn says.
 */
export function isYourTurn(state: ClientGameState): boolean {
  const turn = state.turn;
  if (turn === null) return false;
  const options = yourOptions(state);
  if (options.pick.length > 0 || options.attack.length > 0) return true;
  if ("question" in turn && "your_answer" in turn) {
    if (turn.your_answer !== null) return false;
    // A question is only yours to answer if you are in it: `answered` lists
    // participants who have replied, and the projection only sends a
    // question to a viewer who may answer it or watch it. `you` being seated
    // is the honest test.
    return state.you.player_id !== null;
  }
  return false;
}
