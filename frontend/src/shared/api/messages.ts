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
  errorMessageSchema,
  helloMessageSchema,
  lobbyMessageSchema,
  pickRegionFrameSchema,
  pingFrameSchema,
  pongMessageSchema,
  presenceMessageSchema,
  resyncFrameSchema,
  selectTargetFrameSchema,
  snapshotMessageSchema,
  submitAnswerFrameSchema,
  subscribeFrameSchema,
  surrenderFrameSchema,
  unsubscribeFrameSchema,
  updateMessageSchema,
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
