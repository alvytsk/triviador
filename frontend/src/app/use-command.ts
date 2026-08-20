/**
 * Decision 10's hook, re-exported at the app-conventional path.
 *
 * The real implementation is `shared/api/command.ts` — see its doc comment
 * for why: it has zero app-only dependency (only `send` and
 * `client.onMessage`, both on the public `SocketHandle`), and the three
 * command features (`features/pick-region`, `features/select-target`,
 * `features/submit-answer`) need to call it directly, which steiger's
 * `fsd/forbidden-imports` refuses if the hook lives here instead. This file
 * stays so `@/app/use-command` still resolves for anything at the app layer
 * that reaches for it by that name.
 */
export { type CommandFailure, useCommand } from "@/shared/api";
