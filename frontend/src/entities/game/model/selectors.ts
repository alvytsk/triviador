import {
  type ClientGameState,
  type ClientPlayer,
  type SubmittedValue,
  turnOf,
  type YourOptions,
} from "@/shared/api";

/** Frozen so a future consumer that pushes onto `your_options.pick` cannot
 *  corrupt this shared default for every turnless state afterwards. The
 *  cast recovers the mutable `YourOptions` shape callers expect from
 *  `Object.freeze`'s `Readonly<...>` return type — nothing here is ever
 *  actually mutated, so the shapes agree at runtime even though the types
 *  disagree on paper. */
const NO_OPTIONS: YourOptions = Object.freeze({
  pick: Object.freeze([]),
  attack: Object.freeze([]),
}) as unknown as YourOptions;

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
  return turnOf(state)?.kind ?? null;
}

export function yourOptions(state: ClientGameState): YourOptions {
  return turnOf(state)?.your_options ?? NO_OPTIONS;
}

export function deadlineOf(state: ClientGameState): string | null {
  return turnOf(state)?.deadline_at ?? null;
}

export function deadlineIdOf(state: ClientGameState): number | null {
  return turnOf(state)?.deadline_id ?? null;
}

export function answeredBy(state: ClientGameState): readonly string[] {
  const turn = turnOf(state);
  return turn !== null && "answered" in turn ? turn.answered : [];
}

export function yourAnswer(state: ClientGameState): SubmittedValue | null {
  const turn = turnOf(state);
  return turn !== null && "your_answer" in turn ? turn.your_answer : null;
}

/**
 * The one definition of "you can act right now", so no two screens disagree.
 *
 * Discriminated on `turn.kind` because the projection's participant facts
 * differ by kind, and only two kinds (`expansion_picking`,
 * `battle_target_select`) get a populated `your_options` from the server —
 * see `backend/src/triviador/api/projection/turns.py`. Every
 * question-bearing kind gets a default empty `YourOptions`, so for those
 * kinds this reads the participant fields the projection already publishes
 * for rendering (`attacker_id`, `defender_id`, `contenders`) rather than
 * treating "you have a seat" as license to answer — a seated bystander in
 * someone else's duel is watching, not playing, and the server rejects
 * their answer with `not_your_turn`
 * (`backend/src/triviador/domain/game/reducer.py`).
 *
 * The switch is exhaustive over `Turn`'s seven kinds by construction: the
 * `default` branch assigns `turn` to a `never`-typed local, so an eighth
 * kind that gains no case here is a compile error, not a silent `false`.
 */
export function isYourTurn(state: ClientGameState): boolean {
  const turn = turnOf(state);
  if (turn === null) return false;
  const youId = state.you.player_id;
  switch (turn.kind) {
    case "media_warmup":
      // Nothing to do during warmup — it has no affordance and no question.
      return false;
    case "expansion_picking":
    case "battle_target_select":
      return turn.your_options.pick.length > 0 || turn.your_options.attack.length > 0;
    case "expansion_question": {
      // Spec 1 §3.3: every active player answers an expansion question, so
      // the projection carries no participant list for this kind — unlike
      // the restricted kinds below, "seated and not eliminated" *is* the
      // participant test here, not a stand-in for one.
      const you = youPlayer(state);
      return you !== null && !you.is_eliminated && turn.your_answer === null;
    }
    case "battle_duel":
      // Covers the tiebreak variant too: `tiebreak` is a flag on this same
      // shape, not a different turn kind.
      return (
        youId !== null &&
        (youId === turn.attacker_id || youId === turn.defender_id) &&
        turn.your_answer === null
      );
    case "neutral_challenge":
      return youId !== null && youId === turn.attacker_id && turn.your_answer === null;
    case "final_tiebreak":
      return youId !== null && turn.contenders.includes(youId) && turn.your_answer === null;
    default: {
      const exhaustive: never = turn;
      return exhaustive;
    }
  }
}
