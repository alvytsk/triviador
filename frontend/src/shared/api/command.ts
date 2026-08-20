import { useCallback, useEffect, useRef, useState } from "react";
import type { ErrorCode } from "./generated/errors";
import type { ClientFrame } from "./messages";
import { useSocket } from "./socket-context";

export interface CommandFailure {
  commandId: string;
  code: ErrorCode;
  message: string;
}

let counter = 0;
const newCommandId = () => `c${++counter}-${Math.random().toString(36).slice(2, 8)}`;

/**
 * Decision 10, in one place.
 *
 * `command_id` is transport correlation and nothing else (§8.3): with several
 * actions in flight the client cannot otherwise tell which one a
 * `REGION_NOT_FREE` belongs to. It is never used to retry — a rejected
 * command is a decision, not a hiccup.
 *
 * A command clears from `pending` when the server answers it: an `error`
 * carrying its id, or any `game.update`, which by definition carries the
 * state the command either did or did not produce. That second clause is
 * deliberately broad — the alternative is inspecting the new state to guess
 * whether *this* command caused it, which is the client re-deriving the
 * server's decision, i.e. exactly what this design refuses to do.
 *
 * Lives in `shared/api`, not `app/`, though Task 13's design first reached
 * for `app/use-command.ts`. The only things this hook touches — `send` and
 * `client.onMessage` — are already on the public `SocketHandle` this module
 * exports; there is no cache, no dispatcher, no event bus in here, which is
 * exactly the "zero app-only dependency" test Task 12 used to move
 * `useMediaPrefetch` out of `app/` into `shared/lib`. Task 13 needed the same
 * move for a second, sharper reason: `usePickRegion`, `useSelectTarget` and
 * `useSubmitAnswer` live in `features/*`, and steiger's
 * `fsd/forbidden-imports` unconditionally refuses a `features` module
 * importing from `app` (proved directly, the same way
 * `shared/api/socket-context.ts` proved it for `useSocket`: a probe import
 * of `@/app/socket-provider` from `src/features/**` trips "Forbidden import
 * from higher layer \"app\"."). Since every command feature needs this hook
 * and none of them may reach `app/`, the hook has to live somewhere all
 * three — and the widgets and pages above them — can import legally, which
 * is here. There is no `app/use-command.ts` shim re-exporting it: nothing
 * at the app layer calls this hook directly (the route composes
 * `useNarration` and `gameQueryOptions`, not commands), so a re-export
 * whose only importer was its own test would have existed purely to avoid
 * moving that test file — the test lives beside this module instead
 * (`command.test.tsx`).
 */
export function useCommand() {
  const { send: rawSend, client } = useSocket();
  const [pending, setPending] = useState<ReadonlySet<string>>(new Set());
  const [failure, setFailure] = useState<CommandFailure | null>(null);

  // Mirrors `pending` so `onMessage` — a stable callback registered once per
  // `client`, not re-registered on every `pending` change — can check
  // membership against the latest value rather than whatever `pending` held
  // when the effect last ran. `pending` itself stays membership-guarded via
  // `Set.delete`'s no-op-if-absent; `failure` needs the same guard,
  // explicitly, because unlike `delete` there is no free no-op for "set a
  // field" — every earlier version of this hook set it unconditionally.
  const pendingRef = useRef(pending);
  useEffect(() => {
    pendingRef.current = pending;
  }, [pending]);

  useEffect(() => {
    if (client === null) return;
    return client.onMessage((message) => {
      if (message.type === "error") {
        const commandId = message.command_id;
        // An error with no command_id (a connection-level rejection, not a
        // command rejection) or one this instance never sent is not this
        // instance's to react to — see socket-status.tsx for the former.
        if (commandId !== null && pendingRef.current.has(commandId)) {
          setPending((held) => {
            const next = new Set(held);
            next.delete(commandId);
            return next;
          });
          setFailure({ commandId, code: message.code, message: message.message });
        }
        return;
      }
      if (message.type === "game.update" || message.type === "game.snapshot") {
        setPending(new Set());
      }
    });
  }, [client]);

  const send = useCallback(
    (build: (commandId: string) => ClientFrame): string => {
      const commandId = newCommandId();
      setFailure(null);
      setPending((held) => new Set(held).add(commandId));
      rawSend(build(commandId));
      return commandId;
    },
    [rawSend],
  );

  return { send, pending, failure, clear: () => setFailure(null) };
}
