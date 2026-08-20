import { act, screen, waitFor } from "@testing-library/react";
import { HttpResponse, http } from "msw";
import { describe, expect, it } from "vitest";
import { gameKey, meKey } from "@/entities/game";
import { snapshot } from "../../../../testing/factories";
import { server } from "../../../../testing/msw";
import { renderWithApp } from "../../../../testing/render";
import { LobbyPage } from "./lobby-page";

const GAME = {
  game_id: "g1",
  host_id: "u2",
  map_id: "czechia",
  player_count: 2,
  max_players: 3,
  status: "lobby",
};

const ME = { user_id: "u1", username: "alexey", display_name: "Alexey", role: "player" };

function withLobby(games: unknown[] = [GAME]) {
  server.use(
    http.get("/api/games", () => HttpResponse.json(games)),
    http.get("/api/maps", () => HttpResponse.json([{ map_id: "czechia", region_count: 14 }])),
  );
}

describe("LobbyPage", () => {
  it("paints from REST before the socket has said anything", async () => {
    withLobby();
    renderWithApp(<LobbyPage />);
    expect(await screen.findByText("2 / 3")).toBeInTheDocument();
  });

  it("subscribes to the lobby topic on mount", async () => {
    withLobby();
    const harness = renderWithApp(<LobbyPage />);
    act(() => harness.socket.last().open());
    await waitFor(() =>
      expect(harness.socket.last().frames()).toContainEqual({ type: "subscribe", topic: "lobby" }),
    );
  });

  it("updates in place when a lobby.update arrives", async () => {
    withLobby();
    const harness = renderWithApp(<LobbyPage />);
    await screen.findByText("2 / 3");
    act(() => harness.socket.last().open());
    act(() =>
      harness.socket.last().deliver({
        type: "lobby.update",
        games: [{ ...GAME, player_count: 3 }],
      }),
    );
    expect(await screen.findByText("3 / 3")).toBeInTheDocument();
  });

  it("offers Join for a game with room and refuses one that is full", async () => {
    withLobby([GAME, { ...GAME, game_id: "g2", player_count: 3 }]);
    renderWithApp(<LobbyPage />);
    expect(await screen.findByRole("button", { name: "Join" })).toBeEnabled();
    expect(screen.getByRole("button", { name: "Full" })).toBeDisabled();
  });

  it("shows an empty state rather than a blank panel", async () => {
    withLobby([]);
    renderWithApp(<LobbyPage />);
    expect(await screen.findByText(/no open games/i)).toBeInTheDocument();
  });

  it("surfaces no_default_preset from create rather than a blank screen", async () => {
    withLobby([]);
    server.use(
      http.post("/api/games", () =>
        HttpResponse.json(
          { code: "no_default_preset", message: "no default preset is configured", details: null },
          { status: 409 },
        ),
      ),
    );
    const { findByRole } = renderWithApp(<LobbyPage />);
    await screen.findByText(/no open games/i);
    (await findByRole("button", { name: /create game/i })).click();
    expect(await findByRole("status")).toHaveTextContent("no default preset is configured");
  });

  it("hands a created game off to the router instead of writing the game cache", async () => {
    withLobby([]);
    server.use(
      // Belt and suspenders alongside the `meKey()` seed below: the query
      // client's default `staleTime: Infinity` (see `app/query-client.ts`)
      // should let `_authed`'s `beforeLoad` find the seeded value without
      // ever fetching, but if that assumption is ever wrong, MSW's
      // `onUnhandledRequest: "error"` needs a handler to fail loudly on
      // instead of throwing an unrelated error.
      http.get("/api/auth/me", () => HttpResponse.json(ME)),
      http.post("/api/games", () => HttpResponse.json(snapshot(9, { game_id: "g9" }))),
    );
    const harness = renderWithApp(<LobbyPage />, {
      seed: ({ queryClient }) => queryClient.setQueryData(meKey(), ME),
    });
    await screen.findByText(/no open games/i);
    (await screen.findByRole("button", { name: /create game/i })).click();

    // The navigation half of Task 10's claim.
    await waitFor(() => expect(harness.router.state.location.pathname).toBe("/games/g9"));
    // The cache half — the one the architecture actually rests on. A second
    // writer of `["game", id]` here would be exactly the bug `writeGame`'s
    // one-merge-rule and the `@/app/dispatcher` lint gate exist to prevent;
    // this proves the mutation itself never attempts it.
    expect(harness.queryClient.getQueryData(gameKey("g9"))).toBeUndefined();
  });
});
