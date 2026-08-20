import type { z } from "zod";
import type {
  ClientGameState,
  duelTurnSchema,
  finalTurnSchema,
  neutralTurnSchema,
  pickingTurnSchema,
  questionTurnSchema,
  targetSelectTurnSchema,
  warmupTurnSchema,
} from "./generated/public";
import {
  attackDeclaredEventSchema,
  baseDamagedEventSchema,
  baseDestroyedEventSchema,
  basesAssignedEventSchema,
  defenseHeldEventSchema,
  duelResolvedEventSchema,
  errorMessageSchema,
  finalTiebreakStartedEventSchema,
  gameAbortedEventSchema,
  gameFinishedEventSchema,
  gameStartedEventSchema,
  helloMessageSchema,
  lobbyMessageSchema,
  neutralAttackFailedEventSchema,
  neutralCapturedEventSchema,
  pickRegionFrameSchema,
  picksGrantedEventSchema,
  pingFrameSchema,
  playerAnsweredEventSchema,
  playerGoneEventSchema,
  playerJoinedEventSchema,
  playerLeftEventSchema,
  pongMessageSchema,
  presenceMessageSchema,
  questionPresentedEventSchema,
  questionResolvedEventSchema,
  resyncFrameSchema,
  roundEventSchema,
  scoreChangedEventSchema,
  selectTargetFrameSchema,
  snapshotMessageSchema,
  submitAnswerFrameSchema,
  subscribeFrameSchema,
  surrenderFrameSchema,
  territoryCapturedEventSchema,
  territoryClaimedEventSchema,
  territoryNeutralizedEventSchema,
  tiebreakStartedEventSchema,
  turnEndedEventSchema,
  turnStartedEventSchema,
  unsubscribeFrameSchema,
  updateMessageSchema,
  warmupStartedEventSchema,
} from "./generated/ws";

export type ServerMessage =
  | z.infer<typeof helloMessageSchema>
  | z.infer<typeof pongMessageSchema>
  | z.infer<typeof lobbyMessageSchema>
  | z.infer<typeof snapshotMessageSchema>
  | z.infer<typeof updateMessageSchema>
  | z.infer<typeof presenceMessageSchema>
  | z.infer<typeof errorMessageSchema>;

export type ClientFrame =
  | z.infer<typeof subscribeFrameSchema>
  | z.infer<typeof unsubscribeFrameSchema>
  | z.infer<typeof resyncFrameSchema>
  | z.infer<typeof pingFrameSchema>
  | z.infer<typeof submitAnswerFrameSchema>
  | z.infer<typeof pickRegionFrameSchema>
  | z.infer<typeof selectTargetFrameSchema>
  | z.infer<typeof surrenderFrameSchema>;

/**
 * The seven turn kinds, as a real discriminated union.
 *
 * `clientGameStateSchema`'s `turn` field cannot be expressed as a Zod
 * discriminated union by the code generator: the JSON Schema `oneOf` it
 * compiles from becomes a `z.any().superRefine(...)` that checks "exactly
 * one of these seven schemas parses", so `z.infer` collapses that field to
 * `any`. This is the union the generator could not express, declared by
 * hand from the same seven generated schemas it validates against.
 */
export type Turn =
  | z.infer<typeof warmupTurnSchema>
  | z.infer<typeof questionTurnSchema>
  | z.infer<typeof pickingTurnSchema>
  | z.infer<typeof targetSelectTurnSchema>
  | z.infer<typeof duelTurnSchema>
  | z.infer<typeof neutralTurnSchema>
  | z.infer<typeof finalTurnSchema>;

/**
 * Recovers `state.turn`'s real type.
 *
 * This is a cast, not a re-parse. For a `ClientGameState` that arrived over
 * the wire, `clientGameStateSchema.parse()` has already validated `turn`
 * against exactly one of the seven schemas above, via the `superRefine`
 * described on `Turn` — that check is what makes this cast sound there.
 * (Test fixtures built by `testing/factories.ts` skip that parse entirely
 * and are the caller's responsibility to keep honest; this function trusts
 * its input either way, same as every other selector in this codebase.)
 * The runtime guarantee, where it applies, already holds by the time a
 * `ClientGameState` exists; only the compile-time type was lost to the code
 * generator's `z.any()` fallback. Re-parsing here would re-check a fact
 * that is already established, on every render; this recovers the dropped
 * type instead.
 */
export function turnOf(state: ClientGameState): Turn | null {
  return state.turn as Turn | null;
}

const SERVER_SCHEMAS = {
  hello: helloMessageSchema,
  pong: pongMessageSchema,
  "lobby.snapshot": lobbyMessageSchema,
  "lobby.update": lobbyMessageSchema,
  "game.snapshot": snapshotMessageSchema,
  "game.update": updateMessageSchema,
  "game.presence": presenceMessageSchema,
  error: errorMessageSchema,
} as const;

const CLIENT_SCHEMAS = {
  subscribe: subscribeFrameSchema,
  unsubscribe: unsubscribeFrameSchema,
  resync: resyncFrameSchema,
  ping: pingFrameSchema,
  submit_answer: submitAnswerFrameSchema,
  pick_region: pickRegionFrameSchema,
  select_attack_target: selectTargetFrameSchema,
  surrender: surrenderFrameSchema,
} as const;

export class MessageParseError extends Error {}

/**
 * `null` means "a `type` this build does not know" — ignored on purpose, so a
 * Plan 6 client meeting a Plan 7 message keeps playing. Anything else that is
 * wrong throws: a *known* type whose payload fails its schema is the contract
 * breaking, and swallowing it would turn a loud bug into a board that quietly
 * stops updating.
 */
export function parseServerMessage(raw: string): ServerMessage | null {
  let parsed: unknown;
  try {
    parsed = JSON.parse(raw);
  } catch (cause) {
    throw new MessageParseError("socket sent a frame that is not JSON", { cause });
  }
  if (typeof parsed !== "object" || parsed === null || !("type" in parsed)) {
    throw new MessageParseError("socket sent a frame with no type");
  }
  const type = (parsed as { type: unknown }).type;
  if (typeof type !== "string" || !(type in SERVER_SCHEMAS)) return null;
  const schema = SERVER_SCHEMAS[type as keyof typeof SERVER_SCHEMAS];
  const result = schema.safeParse(parsed);
  if (!result.success) {
    throw new MessageParseError(`socket sent a malformed ${type}`, { cause: result.error });
  }
  return result.data as ServerMessage;
}

/** Outbound frames go through the generated schema too (decision 1). Every
 *  frame schema is `.strict()`, so a stray key — `actor_id` above all — is
 *  caught here rather than arriving as a `validation_failed` with no
 *  `command_id` to correlate it to. */
export function encodeClientFrame(frame: ClientFrame): string {
  const schema = CLIENT_SCHEMAS[frame.type];
  return JSON.stringify(schema.parse(frame));
}

const EVENT_SCHEMAS = {
  attack_declared: attackDeclaredEventSchema,
  base_damaged: baseDamagedEventSchema,
  base_destroyed: baseDestroyedEventSchema,
  bases_assigned: basesAssignedEventSchema,
  defense_held: defenseHeldEventSchema,
  duel_resolved: duelResolvedEventSchema,
  final_tiebreak_started: finalTiebreakStartedEventSchema,
  game_aborted: gameAbortedEventSchema,
  game_finished: gameFinishedEventSchema,
  game_started: gameStartedEventSchema,
  neutral_attack_failed: neutralAttackFailedEventSchema,
  neutral_captured: neutralCapturedEventSchema,
  picks_granted: picksGrantedEventSchema,
  player_answered: playerAnsweredEventSchema,
  player_gone: playerGoneEventSchema,
  player_joined: playerJoinedEventSchema,
  player_left: playerLeftEventSchema,
  question_presented: questionPresentedEventSchema,
  question_resolved: questionResolvedEventSchema,
  round: roundEventSchema,
  score_changed: scoreChangedEventSchema,
  territory_captured: territoryCapturedEventSchema,
  territory_claimed: territoryClaimedEventSchema,
  territory_neutralized: territoryNeutralizedEventSchema,
  tiebreak_started: tiebreakStartedEventSchema,
  turn_ended: turnEndedEventSchema,
  turn_started: turnStartedEventSchema,
  warmup_started: warmupStartedEventSchema,
} as const;

type EventSchemas = typeof EVENT_SCHEMAS;
export type Narration = { [K in keyof EventSchemas]: z.infer<EventSchemas[K]> }[keyof EventSchemas];

/** `null` for an event type this build does not know — narration is
 *  decoration, and a client that throws away an animation it has never heard
 *  of is behaving correctly. A *known* type with a malformed payload also
 *  returns null rather than throwing: unlike a `game.update`, losing one
 *  narration event costs nothing, and taking the whole board down for a bad
 *  toast would be the wrong trade. */
export function parseClientEvent(value: unknown): Narration | null {
  if (typeof value !== "object" || value === null || !("type" in value)) return null;
  const type = (value as { type: unknown }).type;
  if (typeof type !== "string" || !(type in EVENT_SCHEMAS)) return null;
  const result = EVENT_SCHEMAS[type as keyof EventSchemas].safeParse(value);
  return result.success ? (result.data as Narration) : null;
}
