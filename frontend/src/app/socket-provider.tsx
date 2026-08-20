import { useQueryClient } from "@tanstack/react-query";
import {
  createContext,
  type ReactNode,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import { meKey } from "@/entities/game";
import {
  type ClientFrame,
  createSocketClient,
  type Narration,
  type SocketClient,
  type SocketStatus,
} from "@/shared/api";
import { createDispatcher } from "./dispatcher";
import { createEventBus, type EventBus } from "./event-bus";

interface SocketContextValue {
  send(frame: ClientFrame): void;
  status: SocketStatus;
  offsetMs(): number;
  bus: EventBus;
  client: SocketClient | null;
}

const SocketContext = createContext<SocketContextValue | null>(null);

/**
 * §8.1's one multiplexed socket per tab.
 *
 * It is opened once, above the router, and lives for the whole signed-in
 * session — navigating lobby → game → lobby must not reconnect, because a
 * reconnect costs a full resync of every topic and a visible hitch.
 *
 * `4401` is the one close code that changes who you are: the session is gone,
 * so `["me"]` is wrong and the guard has to see that. Clearing the cache
 * entry is enough — the next render sends the guard to `/login`.
 */
export function SocketProvider({
  children,
  enabled,
  client: injected,
}: {
  children: ReactNode;
  enabled: boolean;
  client?: SocketClient;
}) {
  const queryClient = useQueryClient();
  const bus = useMemo(() => createEventBus(), []);
  const dispatcher = useMemo(() => createDispatcher({ queryClient, bus }), [queryClient, bus]);
  const [client, setClient] = useState<SocketClient | null>(injected ?? null);
  const [status, setStatus] = useState<SocketStatus>(injected?.status() ?? "closed");

  useEffect(() => {
    if (injected !== undefined || !enabled) return;
    const url = `${location.protocol === "https:" ? "wss" : "ws"}://${location.host}/ws`;
    const socket = createSocketClient({ url });
    setClient(socket);
    return () => {
      socket.close();
      setClient(null);
    };
  }, [enabled, injected]);

  useEffect(() => {
    if (client === null) return;
    setStatus(client.status());
    const offMessage = client.onMessage((message) => dispatcher.handle(message));
    const offStatus = client.onStatus((next, closed) => {
      setStatus(next);
      if (closed?.code === 4401) queryClient.setQueryData(meKey(), null);
    });
    return () => {
      offMessage();
      offStatus();
    };
  }, [client, dispatcher, queryClient]);

  // `send` and `offsetMs` read the live client through a ref rather than
  // closing over `client` directly, so their identity never changes across
  // a status transition. `useGameSubscription`'s subscribe effect depends on
  // `send`; if it churned on every "connecting" → "open" → "reconnecting"
  // step, that effect would tear down and rebuild on every reconnect,
  // sending a spurious unsubscribe/subscribe pair that has nothing to do
  // with the topics actually changing.
  const clientRef = useRef<SocketClient | null>(client);
  clientRef.current = client;
  const send = useCallback((frame: ClientFrame) => {
    clientRef.current?.send(frame);
  }, []);
  const offsetMs = useCallback(() => clientRef.current?.offsetMs() ?? 0, []);

  const value = useMemo<SocketContextValue>(
    () => ({ send, status, offsetMs, bus, client }),
    [send, status, offsetMs, bus, client],
  );

  return <SocketContext.Provider value={value}>{children}</SocketContext.Provider>;
}

export function useSocket(): SocketContextValue {
  const value = useContext(SocketContext);
  if (value === null) throw new Error("useSocket outside SocketProvider");
  return value;
}

/** Narration for one game. Components never touch the bus directly. */
export function useNarration(gameId: string, listener: (event: Narration) => void): void {
  const { bus } = useSocket();
  const stable = useRef(listener);
  stable.current = listener;
  useEffect(() => bus.subscribe(gameId, (event) => stable.current(event)), [bus, gameId]);
}
