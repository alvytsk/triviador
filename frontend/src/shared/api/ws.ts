// `TIMING` lives in `shared/config` — a different segment of the same
// sliceless `shared` layer, so `fsd/import-locality` wants it relative, not
// `@/shared/config` (the same correction `shared/ui` needed for `shared/lib`).
import { TIMING } from "../config";
import { createClockOffset } from "./clock";
import {
  type ClientFrame,
  encodeClientFrame,
  parseServerMessage,
  type ServerMessage,
} from "./messages";

export type SocketStatus = "connecting" | "open" | "reconnecting" | "closed";
export interface SocketClosed {
  code: number;
}

export interface SocketLike {
  send(data: string): void;
  close(code?: number, reason?: string): void;
  readyState: number;
  onopen: (() => void) | null;
  onclose: ((event: { code: number }) => void) | null;
  onerror: (() => void) | null;
  onmessage: ((event: { data: string }) => void) | null;
}

export interface SocketClient {
  send(frame: ClientFrame): void;
  onMessage(listener: (message: ServerMessage) => void): () => void;
  onStatus(listener: (status: SocketStatus, closed?: SocketClosed) => void): () => void;
  status(): SocketStatus;
  offsetMs(): number;
  close(): void;
}

/**
 * §8.1's one multiplexed socket, and nothing else. It does not know a cache
 * exists, it does not know what a game is, and it must not learn: everything
 * that decides what a message *means* is `app/dispatcher.ts`, one layer up
 * (§9.4).
 *
 * Two close codes are terminal rather than retryable — `4401` (the session is
 * gone) and `4403` (this principal may not have that topic, or the origin was
 * refused). Reconnecting into either would be a client hammering a door it has
 * been told it does not have a key for; §11.1 gives each an explicit reaction,
 * and the reaction is not "try again".
 */
export function createSocketClient(options: {
  url: string;
  socketFactory?: (url: string) => SocketLike;
  now?: () => number;
}): SocketClient {
  const factory =
    options.socketFactory ?? ((url: string) => new WebSocket(url) as unknown as SocketLike);
  const now = options.now ?? Date.now;
  const clock = createClockOffset();

  const messageListeners = new Set<(message: ServerMessage) => void>();
  const statusListeners = new Set<(status: SocketStatus, closed?: SocketClosed) => void>();

  let socket: SocketLike | null = null;
  let current: SocketStatus = "connecting";
  let backoff: number = TIMING.RECONNECT_BASE_MS;
  let pending: string[] = [];
  let pingTimer: ReturnType<typeof setInterval> | null = null;
  let retryTimer: ReturnType<typeof setTimeout> | null = null;
  let pingSentAt = 0;
  let disposed = false;

  const TERMINAL_CLOSE_CODES = new Set([4401, 4403]);

  function setStatus(next: SocketStatus, closed?: SocketClosed): void {
    current = next;
    for (const listener of statusListeners) listener(next, closed);
  }

  function stopTimers(): void {
    if (pingTimer !== null) clearInterval(pingTimer);
    if (retryTimer !== null) clearTimeout(retryTimer);
    pingTimer = null;
    retryTimer = null;
  }

  function connect(): void {
    if (disposed) return;
    const opened = factory(options.url);
    socket = opened;

    opened.onopen = () => {
      backoff = TIMING.RECONNECT_BASE_MS;
      setStatus("open");
      const queued = pending;
      pending = [];
      for (const encoded of queued) opened.send(encoded);
      pingTimer = setInterval(() => {
        pingSentAt = now();
        opened.send(encodeClientFrame({ type: "ping" }));
      }, TIMING.PING_INTERVAL_MS);
    };

    opened.onmessage = (event) => {
      const message = parseServerMessage(event.data);
      if (message === null) return;
      if (message.type === "pong") {
        clock.record(pingSentAt, Date.parse(message.server_time), now());
        return; // §8.6's heartbeat is not application data.
      }
      for (const listener of messageListeners) listener(message);
    };

    // A transport error is always followed by a close; handling both would
    // double-count and halve the backoff.
    opened.onerror = () => {};

    opened.onclose = (event) => {
      stopTimers();
      socket = null;
      if (disposed) {
        // `close()` already emitted the synchronous "closed" status (with
        // code 1000) before driving this handler; emitting again here would
        // double-fire every consumer that reacts to a closed transition.
        return;
      }
      if (TERMINAL_CLOSE_CODES.has(event.code)) {
        disposed = true;
        setStatus("closed", { code: event.code });
        return;
      }
      setStatus("reconnecting", { code: event.code });
      retryTimer = setTimeout(connect, backoff);
      backoff = Math.min(backoff * 2, TIMING.RECONNECT_MAX_MS);
    };
  }

  connect();

  return {
    send(frame) {
      // Encoded — and therefore schema-checked — even when queued, so a
      // malformed frame throws at the call site rather than on reconnect.
      // The encoded string is what gets queued too, so a later flush sends
      // it as-is instead of re-encoding.
      const encoded = encodeClientFrame(frame);
      if (socket !== null && current === "open") socket.send(encoded);
      else pending.push(encoded);
    },
    onMessage(listener) {
      messageListeners.add(listener);
      return () => messageListeners.delete(listener);
    },
    onStatus(listener) {
      statusListeners.add(listener);
      return () => statusListeners.delete(listener);
    },
    status: () => current,
    offsetMs: () => clock.offsetMs(),
    close() {
      disposed = true;
      stopTimers();
      pending = [];
      const open = socket;
      socket = null;
      // Emitted synchronously, with the code, so a caller that reads
      // `status()` right after `close()` never sees a stale value, and every
      // "closed" event carries a code. The `onclose` this drives is a no-op
      // for status (see the `disposed` branch above) so this is the only
      // emission.
      setStatus("closed", { code: 1000 });
      open?.close(1000);
    },
  };
}
