// Re-exports the three generated modules. `pnpm codegen` embeds each
// document's `$defs` independently (§7, and the header comment in
// `scripts/codegen.mjs`) so `rest.schema.json` and `ws.schema.json` each
// carry a full, self-contained copy of the "client state" types they
// share, and `errors.json`'s two enums are redeclared inside both of those
// documents too. The duplicates are byte-identical — deliberate per-module
// tree-shakeability, not drift — but a plain `export *` from all three
// trips TypeScript's ambiguous-re-export check (TS2308) regardless, since
// it compares symbol identity, not shape. The explicit re-exports below
// pin one canonical binding per duplicated name: `errors.ts` wins for the
// two error enums it exists to own, `public.ts` wins for the client-state
// types it shares with `ws.ts`.
//
// To whoever extends this file (Tasks 4, 5, 7): new exports go in the explicit lists
// above. If a newly-duplicated name shows up, it will surface as a TS2308 error rather
// than silently — fix that by pinning the canonical module for that name here, never by
// dropping the name.

export { createClockOffset } from "./clock";
export { type ApiFailureKind, ApiFetchError } from "./errors";
export type { ApiErrorCode, RejectCode } from "./generated/errors";
export * from "./generated/errors";
export { apiErrorCodeSchema, rejectCodeSchema } from "./generated/errors";
export type {
  AcquisitionKind,
  ClientChoice,
  ClientGameState,
  ClientPlayer,
  ClientQuestion,
  ClientRules,
  ClientTerritory,
  ClientYou,
  Difficulty,
  DuelTurn,
  FinalTurn,
  NeutralTurn,
  Phase,
  PickingTurn,
  QuestionKind,
  QuestionTurn,
  SubmittedValue,
  TargetSelectTurn,
  TerritoryKind,
  UserRole,
  WarmupTurn,
  YourOptions,
} from "./generated/public";
export * from "./generated/public";
export {
  acquisitionKindSchema,
  clientChoiceSchema,
  clientGameStateSchema,
  clientPlayerSchema,
  clientQuestionSchema,
  clientRulesSchema,
  clientTerritorySchema,
  clientYouSchema,
  difficultySchema,
  duelTurnSchema,
  finalTurnSchema,
  neutralTurnSchema,
  phaseSchema,
  pickingTurnSchema,
  questionKindSchema,
  questionTurnSchema,
  submittedValueSchema,
  targetSelectTurnSchema,
  territoryKindSchema,
  userRoleSchema,
  warmupTurnSchema,
  yourOptionsSchema,
} from "./generated/public";
export * from "./generated/ws";
export {
  type ClientFrame,
  encodeClientFrame,
  MessageParseError,
  type Narration,
  parseClientEvent,
  parseServerMessage,
  type ServerMessage,
  type Turn,
  turnOf,
} from "./messages";
export { apiFetch, apiSend } from "./rest";
export {
  createSocketClient,
  type SocketClient,
  type SocketClosed,
  type SocketLike,
  type SocketStatus,
} from "./ws";
