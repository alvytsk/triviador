export interface SocketLike {
  send(data: string): void;
  close(code?: number, reason?: string): void;
  readyState: number;
  onopen: (() => void) | null;
  onclose: ((event: { code: number }) => void) | null;
  onerror: (() => void) | null;
  onmessage: ((event: { data: string }) => void) | null;
}

export class FakeSocket implements SocketLike {
  static readonly OPEN = 1;
  static readonly CLOSED = 3;

  readonly sent: string[] = [];
  readyState = 0;
  onopen: (() => void) | null = null;
  onclose: ((event: { code: number }) => void) | null = null;
  onerror: (() => void) | null = null;
  onmessage: ((event: { data: string }) => void) | null = null;

  constructor(readonly url: string) {}

  send(data: string): void {
    this.sent.push(data);
  }

  close(code = 1000): void {
    this.readyState = FakeSocket.CLOSED;
    this.onclose?.({ code });
  }

  // --- test-side controls ---
  open(): void {
    this.readyState = FakeSocket.OPEN;
    this.onopen?.();
  }

  deliver(message: unknown): void {
    this.onmessage?.({ data: JSON.stringify(message) });
  }

  serverClose(code: number): void {
    this.readyState = FakeSocket.CLOSED;
    this.onclose?.({ code });
  }

  /** The frames the client sent, parsed. */
  frames(): Array<Record<string, unknown>> {
    return this.sent.map((raw) => JSON.parse(raw) as Record<string, unknown>);
  }
}

/** Hands every constructed socket back to the test. */
export function fakeSocketFactory() {
  const created: FakeSocket[] = [];
  return {
    created,
    factory: (url: string): SocketLike => {
      const socket = new FakeSocket(url);
      created.push(socket);
      return socket;
    },
    last(): FakeSocket {
      const socket = created.at(-1);
      if (socket === undefined) throw new Error("no socket was created");
      return socket;
    },
  };
}
