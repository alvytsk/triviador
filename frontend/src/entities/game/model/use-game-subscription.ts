import { useEffect, useRef } from "react";
import { type SocketStatus, useSocket } from "@/shared/api";

/**
 * §9.4's refcounted subscription. Two widgets on one screen both wanting the
 * game must produce one `subscribe`, and the last of them to unmount must
 * produce the `unsubscribe` — a naive per-component effect would unsubscribe
 * the whole tab the moment any one of them re-rendered out.
 *
 * The refcount is module-level rather than per-provider because it counts
 * subscriptions on *the* socket, of which §8.1 says there is one.
 *
 * §8.5's reconnect: on reopen the server has forgotten every subscription, so
 * every held topic is re-`subscribe`d — which already answers with a fresh
 * snapshot. `resync` is reserved for a socket that is still open but whose
 * client believes it has desynced (§11.7's "one resolution: take a fresh
 * snapshot"), and is exposed separately as `resyncGame`.
 *
 * This hook (and `useResyncGame` below) lives in `entities/game` rather than
 * `app/`, where it was originally drafted: `GamePage` — a page — needs it,
 * and `pages` may not import from `app` (steiger's `fsd/forbidden-imports`,
 * proven directly the same way `shared/api/socket-context.ts` documents for
 * `useSocket`). It only ever needs `send`, `status` and `client` off the
 * socket — never `EventBus` narration — so it consumes the thin
 * `useSocket`/`SocketHandle` exported from `@/shared/api`, not the richer
 * context `app/socket-provider.tsx` keeps for itself. `app/` still owns the
 * `SocketProvider` that supplies that context, the dispatcher, the event bus
 * and `useNarration` — nothing about *those* needed to move.
 */
const counts = new Map<string, number>();

export function useGameSubscription(gameId: string): void {
  const { send, client, status } = useSocket();

  useEffect(() => {
    const topic = `game:${gameId}`;
    const held = counts.get(topic) ?? 0;
    counts.set(topic, held + 1);
    if (held === 0) send({ type: "subscribe", topic });

    return () => {
      const remaining = (counts.get(topic) ?? 1) - 1;
      if (remaining <= 0) {
        counts.delete(topic);
        send({ type: "unsubscribe", topic });
      } else {
        counts.set(topic, remaining);
      }
    };
  }, [gameId, send]);

  // Re-subscribe after a reconnect — and only after a reconnect. `status`
  // reaching "open" also happens on the very first connect, and effect one,
  // above, already sent that subscribe (queued, and flushed by the socket
  // client the moment it opens). Re-sending it here too would double up on
  // the one case this component's own mount effect already covers, which is
  // exactly what the first test in `use-game-subscription.test.tsx` catches.
  // "reconnecting" is the one status a first connect never passes through —
  // §8.1's client goes "connecting" → "open" once, and "open" →
  // "reconnecting" → "open" on every drop after that — so gating on the
  // *previous* status having been "reconnecting" is what distinguishes them.
  const previousStatus = useRef<SocketStatus>(status);
  useEffect(() => {
    const was = previousStatus.current;
    previousStatus.current = status;
    if (status !== "open" || client === null || was !== "reconnecting") return;
    const topic = `game:${gameId}`;
    if ((counts.get(topic) ?? 0) > 0) send({ type: "subscribe", topic });
  }, [status, client, gameId, send]);
}

/** §11.7: any client-side desync has exactly one resolution. */
export function useResyncGame(gameId: string): () => void {
  const { send } = useSocket();
  return () => send({ type: "resync", topic: `game:${gameId}` });
}
