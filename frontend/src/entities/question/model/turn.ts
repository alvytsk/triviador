import { type ClientGameState, type ClientQuestion, turnOf } from "@/shared/api";

/** The seven turn kinds are a union; four of them carry a question. This is
 *  the narrowing, written once. */
export function questionOf(state: ClientGameState): ClientQuestion | null {
  const turn = turnOf(state);
  return turn !== null && "question" in turn ? turn.question : null;
}
