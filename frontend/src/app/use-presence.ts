import { createContext, useContext, useSyncExternalStore } from "react";

/**
 * §8.3, verbatim: "deliberately not a domain event — no `seq`, not
 * persisted, absent from replay." Putting a `game.presence` message beside
 * `GameSnapshot` in the query cache would be the one place a reader could
 * mistake it for state, so it gets a store of its own instead — a small
 * `useSyncExternalStore`-backed map, fed by `app/dispatcher.ts` (the one
 * writer, the same way `EventBus` has exactly one emitter) and read by
 * `usePresence` below.
 *
 * `snapshot(gameId)` returns `null` until the first `game.presence` for that
 * game has actually arrived — distinct from `[]`, which is the server
 * saying nobody is connected. A player strip that treated "no message yet"
 * the same as "empty roster" would dim every seat for the instant between
 * mount and the server's first presence push, which is exactly the kind of
 * flicker §9.5 exists to rule out.
 */
export interface PresenceStore {
  update(gameId: string, connected: readonly string[]): void;
  subscribe(gameId: string, listener: () => void): () => void;
  snapshot(gameId: string): readonly string[] | null;
}

export function createPresenceStore(): PresenceStore {
  const connected = new Map<string, readonly string[]>();
  const listeners = new Map<string, Set<() => void>>();

  return {
    update(gameId, players) {
      connected.set(gameId, players);
      const forGame = listeners.get(gameId);
      if (forGame === undefined) return;
      for (const listener of [...forGame]) listener();
    },
    subscribe(gameId, listener) {
      const forGame = listeners.get(gameId) ?? new Set();
      forGame.add(listener);
      listeners.set(gameId, forGame);
      return () => {
        forGame.delete(listener);
        if (forGame.size === 0) listeners.delete(gameId);
      };
    },
    snapshot(gameId) {
      return connected.get(gameId) ?? null;
    },
  };
}

/**
 * `SocketProvider` provides one `PresenceStore` for the life of the tab —
 * see that file for why a *second*, narrower context (mirroring
 * `shared/api/socket-context.ts`'s split from the richer one
 * `socket-provider.tsx` keeps for itself) is not needed here: this context
 * is never read below `app/` at all. `usePresence` is behind the same
 * `fsd/forbidden-imports` wall as `useNarration` (`@/app/use-presence` is a
 * higher-layer import from `pages`, `widgets` or `features`), so the one
 * caller is `app/routes/_authed.games.$gameId.tsx`, which hands the result
 * down through `<GamePage>` as a prop — the same shape Task 13 used for
 * `question_resolved`.
 */
const PresenceContext = createContext<PresenceStore | null>(null);
export const PresenceProvider = PresenceContext.Provider;

/** The last `game.presence` for one game, or `null` before the first one
 *  has arrived (see `PresenceStore`'s doc comment above). */
export function usePresence(gameId: string): readonly string[] | null {
  const store = useContext(PresenceContext);
  if (store === null) throw new Error("usePresence outside PresenceProvider");
  return useSyncExternalStore(
    (onStoreChange) => store.subscribe(gameId, onStoreChange),
    () => store.snapshot(gameId),
  );
}
