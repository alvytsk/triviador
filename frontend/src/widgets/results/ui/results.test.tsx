import { screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { gameState, player } from "../../../../testing/factories";
import { renderWithApp } from "../../../../testing/render";
import { Results } from "./results";

// `winner_id` names u1 even though u1 has the *lowest* score alone — the
// server's decision, never re-derived. And u1's `score + bonus_score`
// (1900) would outrank everyone if the client ever summed them; ranked by
// `score` alone, u1 comes last. Both properties this widget must hold are
// visible in one fixture.
const FINISHED = gameState({
  phase: "finished",
  winner_id: "u1",
  turn: null,
  players: [
    player({ player_id: "u1", display_name: "Alexey", seat: 0, score: 1200, bonus_score: 700 }),
    player({ player_id: "u2", display_name: "Petra", seat: 1, score: 1300, bonus_score: 0 }),
    player({
      player_id: "u3",
      display_name: "Tomáš",
      seat: 2,
      score: 1250,
      bonus_score: 0,
      is_eliminated: true,
    }),
  ],
});

describe("Results", () => {
  it("names the winner from winner_id, not the top scorer", () => {
    renderWithApp(<Results state={FINISHED} />);
    expect(screen.getByTestId("results-winner")).toHaveTextContent("Alexey");
  });

  it("ranks every player by score alone — never score + bonus_score", () => {
    renderWithApp(<Results state={FINISHED} />);
    const order = screen.getAllByRole("listitem").map((li) => li.dataset.testid);
    // Petra (1300) > Tomáš (1250) > Alexey (1200) by score. Summed with
    // bonus, Alexey (1900) would be first — the opposite of last.
    expect(order).toEqual(["result-u2", "result-u3", "result-u1"]);
  });

  it("shows score and bonus_score separately — never added together", () => {
    renderWithApp(<Results state={FINISHED} />);
    const row = screen.getByTestId("result-u1");
    expect(row).toHaveTextContent("1200");
    expect(row).toHaveTextContent("+700 bonus");
    expect(row).not.toHaveTextContent("1900");
  });

  it("marks only the winner's row with a WINNER chip", () => {
    renderWithApp(<Results state={FINISHED} />);
    expect(screen.getByTestId("result-u1")).toHaveTextContent("WINNER");
    expect(screen.getByTestId("result-u2")).not.toHaveTextContent("WINNER");
  });

  it("links back to the lobby", () => {
    renderWithApp(<Results state={FINISHED} />);
    expect(screen.getByRole("link", { name: /back to the lobby/i })).toHaveAttribute("href", "/");
  });

  it("names the game_aborted reason when one arrived", () => {
    const aborted = gameState({ phase: "aborted", turn: null });
    renderWithApp(
      <Results
        state={aborted}
        aborted={{ type: "game_aborted", reason: "A player disconnected too long." }}
      />,
    );
    expect(screen.getByText("A player disconnected too long.")).toBeInTheDocument();
  });

  it("falls back to a generic message when the screen opened after the event", () => {
    const aborted = gameState({ phase: "aborted", turn: null });
    renderWithApp(<Results state={aborted} />);
    expect(screen.getByText("This game was ended.")).toBeInTheDocument();
  });
});
