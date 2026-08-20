import { createContext, useContext } from "react";
import type { ClientFrame } from "./messages";
import type { SocketClient, SocketStatus } from "./ws";

/**
 * The thin slice of `SocketProvider`'s context that a screen *below* `app/`
 * is allowed to reach: send a frame, read the connection status, read the
 * clock offset, or read the client itself. `EventBus` narration is
 * deliberately not part of this shape — `app/socket-provider.tsx` keeps its
 * own richer context (this same value plus `bus`) for `useNarration` and
 * `use-game-subscription.ts`, because giving narration to every layer would
 * have to drag `EventBus`'s type down here with it, for no consumer this
 * plan has yet.
 *
 * This lives in `shared/api` — next to `ws.ts`, which defines every type
 * used below — rather than being the context `app/socket-provider.tsx`
 * itself exports, because FSD's layer sequence is
 * `shared < entities < features < widgets < pages < app`: nothing below
 * `app` may import from it, and `steiger`'s `fsd/forbidden-imports` fails
 * `pnpm check` the moment it does (proven directly: a probe component under
 * `src/pages` importing `useSocket` from `@/app/socket-provider` trips
 * "Forbidden import from higher layer \"app\"."). A screen like
 * `pages/lobby`'s `LobbyPage`, which sends its own `subscribe`/`unsubscribe`
 * frames as a plain effect (§9.4: the `lobby` topic is held by exactly one
 * screen, so it does not need `useGameSubscription`'s refcount), needs a
 * `useSocket` it is actually allowed to call.
 *
 * `SocketProvider` provides both contexts with the same underlying value
 * object — this one is the public slice of it.
 */
export interface SocketHandle {
  send(frame: ClientFrame): void;
  status: SocketStatus;
  offsetMs(): number;
  client: SocketClient | null;
}

export const SocketConnectionContext = createContext<SocketHandle | null>(null);

export function useSocket(): SocketHandle {
  const value = useContext(SocketConnectionContext);
  if (value === null) throw new Error("useSocket outside SocketProvider");
  return value;
}
