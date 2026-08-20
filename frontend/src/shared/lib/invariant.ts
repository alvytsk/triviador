/**
 * Narrowing that throws rather than a comment that hopes.
 *
 * Used for facts the projection guarantees but the generated types cannot
 * express — e.g. a `questionTurn` always carries a `question`, but a viewer
 * holding `ClientGameState["turn"]` has a seven-member union until something
 * narrows it.
 */
export function invariant(condition: unknown, message: string): asserts condition {
  if (!condition) {
    throw new Error(`invariant: ${message}`);
  }
}
