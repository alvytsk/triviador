import type { Narration } from "@/shared/api";

/**
 * §9.1's second sink. Narration — toasts, the capture animation, the battle
 * log — and nothing that outlives a frame.
 *
 * It has no history on purpose. An event bus that replays is a second store,
 * and a second store is the thing this whole design exists to not have. A
 * component that mounts late has missed the animation; it reads the state
 * for the facts.
 */
export interface EventBus {
  emit(gameId: string, events: readonly Narration[]): void;
  subscribe(gameId: string, listener: (event: Narration) => void): () => void;
}

export function createEventBus(): EventBus {
  const listeners = new Map<string, Set<(event: Narration) => void>>();

  return {
    emit(gameId, events) {
      const forGame = listeners.get(gameId);
      if (forGame === undefined) return;
      for (const event of events) {
        for (const listener of [...forGame]) {
          try {
            listener(event);
          } catch (error) {
            // A broken toast must not stop the battle log from rendering,
            // and must never propagate into the socket's message handler —
            // which is the call stack this runs on.
            console.error("narration listener threw", error);
          }
        }
      }
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
  };
}
