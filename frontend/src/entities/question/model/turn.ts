import type { ClientGameState, ClientQuestion } from "@/shared/api";

/** The seven turn kinds are a union; four of them carry a question. This is
 *  the narrowing, written once. */
export function questionOf(state: ClientGameState): ClientQuestion | null {
  const turn = state.turn;
  return turn !== null && "question" in turn ? turn.question : null;
}

export function isNumericTurn(state: ClientGameState): boolean {
  return questionOf(state)?.kind === "numeric";
}
