import { QueryClient } from "@tanstack/react-query";
import { HttpResponse, http } from "msw";
import { describe, expect, it } from "vitest";
import { gameKey } from "@/entities/game";
import { snapshot } from "../../testing/factories";
import { server } from "../../testing/msw";
import { gameQueryOptions } from "./game-query";

/**
 * `gameQueryOptions` — not `GamePage` — is what actually owns the race
 * described in §9.3: a REST response can resolve after a newer `game.update`
 * has already landed via the socket. `GamePage` cannot exercise this itself
 * any more (see the comment on `app/routes/_authed.games.$gameId.tsx`): it
 * receives an already-running query as a prop, and the query's own merge
 * behaviour lives entirely in this function's queryFn. Proving it here,
 * directly against `QueryClient.fetchQuery`, is more honest than proving it
 * indirectly through a component render.
 */
describe("gameQueryOptions", () => {
  it("writes the fetched snapshot into the cache on first load", async () => {
    const queryClient = new QueryClient();
    server.use(http.get("/api/games/g1", () => HttpResponse.json(snapshot(3))));

    const result = await queryClient.fetchQuery(gameQueryOptions("g1", queryClient));

    expect(result.seq).toBe(3);
    expect(queryClient.getQueryData(gameKey("g1"))).toEqual(result);
  });

  it("does not roll back a newer value a socket update already wrote", async () => {
    const queryClient = new QueryClient();
    // Simulates the dispatcher having already applied a newer `game.update`
    // (seq 9) by the time this REST fetch — for an older seq 5 — resolves.
    queryClient.setQueryData(gameKey("g1"), snapshot(9));
    server.use(http.get("/api/games/g1", () => HttpResponse.json(snapshot(5))));

    const result = await queryClient.fetchQuery(gameQueryOptions("g1", queryClient));

    expect(result.seq).toBe(9);
    expect(queryClient.getQueryData(gameKey("g1"))).toEqual(snapshot(9));
  });

  it("still lands a resync that repeats the cache's own seq", async () => {
    // `writeGame`'s `>=`, not `>`: a resync can legitimately carry the seq
    // already held while rebuilding the state underneath it.
    const queryClient = new QueryClient();
    queryClient.setQueryData(gameKey("g1"), snapshot(5, { round_no: 1 }));
    server.use(http.get("/api/games/g1", () => HttpResponse.json(snapshot(5, { round_no: 2 }))));

    const result = await queryClient.fetchQuery(gameQueryOptions("g1", queryClient));

    expect(result.state.round_no).toBe(2);
  });
});
