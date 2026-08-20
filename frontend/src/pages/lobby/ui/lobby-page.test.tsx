import { act, screen, waitFor } from "@testing-library/react";
import { HttpResponse, http } from "msw";
import { describe, expect, it } from "vitest";
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
});
