import { screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { gameState, player } from "../../../../testing/factories";
import { renderWithApp } from "../../../../testing/render";
import { PlayerStrip } from "./player-strip";

const STATE = gameState({
  players: [player(), player({ player_id: "u2", display_name: "Petra", seat: 1 })],
});

describe("PlayerStrip", () => {
  it("dims nobody before the first game.presence — connectedPlayerIds is null", () => {
    renderWithApp(<PlayerStrip state={STATE} connectedPlayerIds={null} />);
    expect(screen.getByTestId("player-u1").className).not.toContain("opacity-40");
    expect(screen.getByTestId("player-u2").className).not.toContain("opacity-40");
  });

  it("dims exactly the players absent from connectedPlayerIds", () => {
    renderWithApp(<PlayerStrip state={STATE} connectedPlayerIds={["u1"]} />);
    expect(screen.getByTestId("player-u1").className).not.toContain("opacity-40");
    expect(screen.getByTestId("player-u2").className).toContain("opacity-40");
  });

  it("dims everyone when the server says the roster is empty", () => {
    renderWithApp(<PlayerStrip state={STATE} connectedPlayerIds={[]} />);
    expect(screen.getByTestId("player-u1").className).toContain("opacity-40");
    expect(screen.getByTestId("player-u2").className).toContain("opacity-40");
  });
});
