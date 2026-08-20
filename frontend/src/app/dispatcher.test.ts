import { QueryClient } from "@tanstack/react-query";
import { describe, expect, it } from "vitest";
import { gameKey, lobbyKey } from "@/entities/game";
import type { GameSnapshot, ServerMessage } from "@/shared/api";
import { snapshot } from "../../testing/factories";
import { createDispatcher, writeGame } from "./dispatcher";
import { createEventBus } from "./event-bus";
import { createPresenceStore } from "./use-presence";

// The brief's fixtures carried only `type`, `region_id`, and the actor id;
// the generated schemas (drifted since the brief was written) also require
// `acquisition` on both events and `automatic` on `territory_claimed`. Added
// here to keep the fixtures parseable under `.strict()` — the test intent
// (ordering, gap suppression, dedup) is unchanged.
const CLAIMED = {
  type: "territory_claimed" as const,
  region_id: "praha",
  player_id: "u1",
  acquisition: "claimed" as const,
  automatic: false,
};
const CAPTURED = {
  type: "territory_captured" as const,
  region_id: "praha",
  to_player_id: "u1",
  from_player_id: "u2",
  acquisition: "conquest" as const,
};

function setup() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const bus = createEventBus();
  const presence = createPresenceStore();
  const dispatcher = createDispatcher({ queryClient, bus, presence });
  const narrated: unknown[] = [];
  bus.subscribe("g1", (event) => narrated.push(event));
  return { queryClient, bus, presence, dispatcher, narrated };
}

function update(seq: number, baseSeq: number, events: unknown[] = []): ServerMessage {
  const { state } = snapshot(seq);
  return {
    type: "game.update",
    game_id: "g1",
    seq,
    base_seq: baseSeq,
    state,
    events,
  } as ServerMessage;
}

function cached(queryClient: QueryClient): GameSnapshot | undefined {
  return queryClient.getQueryData<GameSnapshot>(gameKey("g1"));
}

describe("writeGame", () => {
  it("writes when the cache is empty", () => {
    const queryClient = new QueryClient();
    writeGame(queryClient, "g1", snapshot(3));
    expect(cached(queryClient)?.seq).toBe(3);
  });

  it("writes a newer seq over an older one", () => {
    const queryClient = new QueryClient();
    writeGame(queryClient, "g1", snapshot(3));
    writeGame(queryClient, "g1", snapshot(4));
    expect(cached(queryClient)?.seq).toBe(4);
  });

  it("refuses an older seq — §9.3's REST-lands-after-the-socket race", () => {
    const queryClient = new QueryClient();
    writeGame(queryClient, "g1", snapshot(7));
    writeGame(queryClient, "g1", snapshot(5));
    expect(cached(queryClient)?.seq).toBe(7);
  });

  it("accepts an equal seq, so a resync after a reconnect always lands", () => {
    const queryClient = new QueryClient();
    writeGame(queryClient, "g1", snapshot(7));
    const fresh = snapshot(7, { round_no: 3 });
    writeGame(queryClient, "g1", fresh);
    expect(cached(queryClient)?.state.round_no).toBe(3);
  });
});

describe("the dispatcher", () => {
  it("applies a snapshot and narrates nothing", () => {
    const { dispatcher, queryClient, narrated } = setup();
    dispatcher.handle({
      type: "game.snapshot",
      game_id: "g1",
      seq: 5,
      state: snapshot(5).state,
    } as ServerMessage);
    expect(cached(queryClient)?.seq).toBe(5);
    expect(narrated).toEqual([]);
  });

  it("applies an in-order update and narrates its events", () => {
    const { dispatcher, queryClient, narrated } = setup();
    dispatcher.handle({
      type: "game.snapshot",
      game_id: "g1",
      seq: 5,
      state: snapshot(5).state,
    } as ServerMessage);
    dispatcher.handle(update(6, 5, [CLAIMED]));
    expect(cached(queryClient)?.seq).toBe(6);
    expect(narrated).toEqual([CLAIMED]);
  });

  it("ignores a duplicate entirely — no write, no narration", () => {
    const { dispatcher, queryClient, narrated } = setup();
    dispatcher.handle({
      type: "game.snapshot",
      game_id: "g1",
      seq: 6,
      state: snapshot(6).state,
    } as ServerMessage);
    dispatcher.handle(update(6, 5, [CLAIMED]));
    expect(cached(queryClient)?.seq).toBe(6);
    expect(narrated).toEqual([]);
  });

  it("ignores an update older than the cache", () => {
    const { dispatcher, queryClient, narrated } = setup();
    dispatcher.handle({
      type: "game.snapshot",
      game_id: "g1",
      seq: 9,
      state: snapshot(9).state,
    } as ServerMessage);
    dispatcher.handle(update(7, 6, [CLAIMED]));
    expect(cached(queryClient)?.seq).toBe(9);
    expect(narrated).toEqual([]);
  });

  it("applies a gapped update's state but suppresses its events", () => {
    const { dispatcher, queryClient, narrated } = setup();
    dispatcher.handle({
      type: "game.snapshot",
      game_id: "g1",
      seq: 5,
      state: snapshot(5).state,
    } as ServerMessage);
    dispatcher.handle(update(9, 8, [CLAIMED, CAPTURED]));
    expect(cached(queryClient)?.seq).toBe(9);
    expect(narrated).toEqual([]);
  });

  it("narrates again once the sequence is contiguous after a gap", () => {
    const { dispatcher, narrated } = setup();
    dispatcher.handle({
      type: "game.snapshot",
      game_id: "g1",
      seq: 5,
      state: snapshot(5).state,
    } as ServerMessage);
    dispatcher.handle(update(9, 8, [CLAIMED]));
    dispatcher.handle(update(10, 9, [CAPTURED]));
    expect(narrated).toEqual([CAPTURED]);
  });

  it("suppresses events for the first update when there was no base at all", () => {
    const { dispatcher, queryClient, narrated } = setup();
    dispatcher.handle(update(4, 3, [CLAIMED]));
    expect(cached(queryClient)?.seq).toBe(4);
    expect(narrated).toEqual([]);
  });

  it("never lets an event reach the cache", () => {
    const { dispatcher, queryClient } = setup();
    dispatcher.handle({
      type: "game.snapshot",
      game_id: "g1",
      seq: 5,
      state: snapshot(5).state,
    } as ServerMessage);
    const before = cached(queryClient)?.state;
    dispatcher.handle(update(6, 5, [CLAIMED, CAPTURED]));
    // The only difference between the two states is what the *server* sent;
    // §9.1's whole point is that the client never folds an event in.
    expect(cached(queryClient)?.state).toEqual({ ...before, ...snapshot(6).state });
  });

  it("drops an event type it does not know without dropping the ones it does", () => {
    const { dispatcher, narrated } = setup();
    dispatcher.handle({
      type: "game.snapshot",
      game_id: "g1",
      seq: 5,
      state: snapshot(5).state,
    } as ServerMessage);
    dispatcher.handle(update(6, 5, [{ type: "invented_by_plan_9" }, CLAIMED]));
    expect(narrated).toEqual([CLAIMED]);
  });

  it("keeps two games' sequences independent", () => {
    const { dispatcher, queryClient } = setup();
    dispatcher.handle({
      type: "game.snapshot",
      game_id: "g1",
      seq: 9,
      state: snapshot(9).state,
    } as ServerMessage);
    dispatcher.handle({
      type: "game.snapshot",
      game_id: "g2",
      seq: 2,
      state: snapshot(2).state,
    } as ServerMessage);
    expect(queryClient.getQueryData<GameSnapshot>(gameKey("g1"))?.seq).toBe(9);
    expect(queryClient.getQueryData<GameSnapshot>(gameKey("g2"))?.seq).toBe(2);
  });

  it("writes a lobby message straight to the lobby key", () => {
    const { dispatcher, queryClient } = setup();
    const games = [
      {
        game_id: "g1",
        host_id: "u1",
        map_id: "czechia",
        player_count: 2,
        max_players: 3,
        status: "lobby",
      },
    ];
    dispatcher.handle({ type: "lobby.snapshot", games } as ServerMessage);
    expect(queryClient.getQueryData(lobbyKey())).toEqual(games);
  });

  it("ignores an error message entirely — it never reaches a cache", () => {
    const { dispatcher, queryClient } = setup();
    dispatcher.handle({
      type: "error",
      code: "not_your_turn",
      message: "no",
      command_id: "c1",
    } as ServerMessage);
    expect(cached(queryClient)).toBeUndefined();
  });

  it("feeds game.presence into the presence store, never the query cache", () => {
    const { dispatcher, queryClient, presence } = setup();
    dispatcher.handle({ type: "game.presence", game_id: "g1", connected: ["u1"] } as ServerMessage);
    expect(cached(queryClient)).toBeUndefined();
    expect(presence.snapshot("g1")).toEqual(["u1"]);
  });
});

describe("the event bus", () => {
  it("delivers only to subscribers of that game", () => {
    const bus = createEventBus();
    const one: unknown[] = [];
    const two: unknown[] = [];
    bus.subscribe("g1", (e) => one.push(e));
    bus.subscribe("g2", (e) => two.push(e));
    bus.emit("g1", [CLAIMED]);
    expect(one).toEqual([CLAIMED]);
    expect(two).toEqual([]);
  });

  it("stops delivering after unsubscribe", () => {
    const bus = createEventBus();
    const seen: unknown[] = [];
    const off = bus.subscribe("g1", (e) => seen.push(e));
    off();
    bus.emit("g1", [CLAIMED]);
    expect(seen).toEqual([]);
  });

  it("keeps nothing: a subscriber that arrives late sees no history", () => {
    const bus = createEventBus();
    bus.emit("g1", [CLAIMED]);
    const seen: unknown[] = [];
    bus.subscribe("g1", (e) => seen.push(e));
    expect(seen).toEqual([]);
  });

  it("does not let one throwing subscriber rob the others", () => {
    const bus = createEventBus();
    const seen: unknown[] = [];
    bus.subscribe("g1", () => {
      throw new Error("a toast blew up");
    });
    bus.subscribe("g1", (e) => seen.push(e));
    expect(() => bus.emit("g1", [CLAIMED])).not.toThrow();
    expect(seen).toEqual([CLAIMED]);
  });
});
