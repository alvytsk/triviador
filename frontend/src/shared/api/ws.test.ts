import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { fakeSocketFactory } from "../../../testing/fake-socket";
import { TIMING } from "../config";
import { createSocketClient } from "./ws";

function setup() {
  const sockets = fakeSocketFactory();
  const client = createSocketClient({
    url: "/ws",
    socketFactory: sockets.factory,
    now: () => Date.now(),
  });
  return { sockets, client };
}

beforeEach(() => {
  vi.useFakeTimers();
});

afterEach(() => {
  vi.useRealTimers();
});

describe("createSocketClient", () => {
  it("reports connecting, then open", () => {
    const { sockets, client } = setup();
    const seen: string[] = [];
    client.onStatus((status) => seen.push(status));
    expect(client.status()).toBe("connecting");
    sockets.last().open();
    expect(seen).toContain("open");
    client.close();
  });

  it("delivers a parsed message to listeners", () => {
    const { sockets, client } = setup();
    const received: unknown[] = [];
    client.onMessage((message) => received.push(message));
    sockets.last().open();
    sockets.last().deliver({ type: "game.presence", game_id: "g1", connected: ["u1"] });
    expect(received).toEqual([{ type: "game.presence", game_id: "g1", connected: ["u1"] }]);
    client.close();
  });

  it("ignores a message type it does not know instead of throwing", () => {
    const { sockets, client } = setup();
    const received: unknown[] = [];
    client.onMessage((message) => received.push(message));
    sockets.last().open();
    expect(() => sockets.last().deliver({ type: "admin.something", x: 1 })).not.toThrow();
    expect(received).toEqual([]);
    client.close();
  });

  it("queues frames sent before open and flushes them on open", () => {
    const { sockets, client } = setup();
    client.send({ type: "subscribe", topic: "lobby" });
    expect(sockets.last().sent).toEqual([]);
    sockets.last().open();
    expect(sockets.last().frames()).toEqual([{ type: "subscribe", topic: "lobby" }]);
    client.close();
  });

  it("refuses to send a frame the generated schema rejects", () => {
    const { sockets, client } = setup();
    sockets.last().open();
    expect(() =>
      // @ts-expect-error — the point is that the runtime rejects it too
      client.send({ type: "subscribe", topic: "lobby", actor_id: "u1" }),
    ).toThrow();
    client.close();
  });

  it("pings on the heartbeat interval and folds the pong into the offset", () => {
    const { sockets, client } = setup();
    sockets.last().open();
    vi.advanceTimersByTime(TIMING.PING_INTERVAL_MS);
    expect(sockets.last().frames().at(-1)).toEqual({ type: "ping" });

    const serverTime = new Date(Date.now() + 5_000).toISOString();
    sockets.last().deliver({ type: "pong", server_time: serverTime });
    expect(client.offsetMs()).toBeGreaterThan(4_000);
    client.close();
  });

  it("reconnects with backoff after an unexpected close", () => {
    const { sockets, client } = setup();
    sockets.last().open();
    expect(sockets.created).toHaveLength(1);

    sockets.last().serverClose(1006);
    expect(client.status()).toBe("reconnecting");
    vi.advanceTimersByTime(TIMING.RECONNECT_BASE_MS);
    expect(sockets.created).toHaveLength(2);

    sockets.last().serverClose(1006);
    vi.advanceTimersByTime(TIMING.RECONNECT_BASE_MS);
    expect(sockets.created).toHaveLength(2); // not yet — the delay doubled
    vi.advanceTimersByTime(TIMING.RECONNECT_BASE_MS * 2);
    expect(sockets.created).toHaveLength(3);
    client.close();
  });

  it("resets the backoff once a connection stays open", () => {
    const { sockets, client } = setup();
    sockets.last().open();
    sockets.last().serverClose(1006);
    vi.advanceTimersByTime(TIMING.RECONNECT_BASE_MS);
    sockets.last().open();
    sockets.last().serverClose(1006);
    vi.advanceTimersByTime(TIMING.RECONNECT_BASE_MS);
    expect(sockets.created).toHaveLength(3);
    client.close();
  });

  it("does not reconnect after 4401 or 4403", () => {
    for (const code of [4401, 4403]) {
      const { sockets, client } = setup();
      sockets.last().open();
      sockets.last().serverClose(code);
      vi.advanceTimersByTime(TIMING.RECONNECT_MAX_MS * 4);
      expect(sockets.created).toHaveLength(1);
      expect(client.status()).toBe("closed");
      client.close();
    }
  });

  it("reports the close code to status listeners so a 4401 can sign the user out", () => {
    const { sockets, client } = setup();
    const codes: Array<number | undefined> = [];
    client.onStatus((_status, closed) => codes.push(closed?.code));
    sockets.last().open();
    sockets.last().serverClose(4401);
    expect(codes).toContain(4401);
    client.close();
  });

  it("stops pinging and never reconnects after close()", () => {
    const { sockets, client } = setup();
    sockets.last().open();
    client.close();
    vi.advanceTimersByTime(TIMING.PING_INTERVAL_MS * 5);
    expect(sockets.created).toHaveLength(1);
    expect(client.status()).toBe("closed");
  });
});
