import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { HttpResponse, http } from "msw";
import { describe, expect, it } from "vitest";
import { snapshot } from "../../../../testing/factories";
import { server } from "../../../../testing/msw";
import { renderWithApp } from "../../../../testing/render";
import { CreateGamePanel } from "./create-game-panel";

const MAP = { map_id: "czechia", region_count: 14 };

const rules = (overrides: Partial<Record<string, unknown>> = {}) => ({
  player_count: 4,
  expansion_rounds: 3,
  battle_rounds: 2,
  base_hp: 100,
  answer_timeout_ms: 15000,
  pick_timeout_ms: 10000,
  warmup_ms: 5000,
  claims_by_rank: [3, 2, 1],
  pts_base: 10,
  pts_territory: 5,
  pts_conquered: 20,
  pts_defense: 15,
  ...overrides,
});

const DEFAULT_PRESET = {
  id: "default",
  name: "Default",
  is_default: true,
  rules: rules(),
};

const BLITZ_PRESET = {
  id: "blitz",
  name: "Blitz",
  is_default: false,
  rules: rules({ player_count: 2, battle_rounds: 1 }),
};

function withPresets(presets: unknown[] = [DEFAULT_PRESET, BLITZ_PRESET]) {
  server.use(
    http.get("/api/maps", () => HttpResponse.json([MAP])),
    http.get("/api/presets", () => HttpResponse.json(presets)),
  );
}

describe("CreateGamePanel", () => {
  it("lists the active presets and defaults to the one flagged is_default", async () => {
    withPresets();
    renderWithApp(<CreateGamePanel />);
    const select = (await screen.findByLabelText(/rules/i)) as HTMLSelectElement;
    await waitFor(() => expect(select.value).toBe("default"));
    expect(within(select).getByRole("option", { name: "Default" })).toBeInTheDocument();
    expect(within(select).getByRole("option", { name: "Blitz" })).toBeInTheDocument();
  });

  it("sends the selected preset's id as preset_id instead of null", async () => {
    withPresets();
    let sentBody: unknown;
    server.use(
      http.post("/api/games", async ({ request }) => {
        sentBody = await request.json();
        return HttpResponse.json(snapshot(9, { game_id: "g9" }));
      }),
    );
    renderWithApp(<CreateGamePanel />);
    const select = (await screen.findByLabelText(/rules/i)) as HTMLSelectElement;
    await waitFor(() => expect(select.value).toBe("default"));

    await userEvent.selectOptions(select, "blitz");
    await userEvent.click(await screen.findByRole("button", { name: /create game/i }));

    await waitFor(() => expect(sentBody).toMatchObject({ map_id: "czechia", preset_id: "blitz" }));
  });

  it("reflects the selected preset in the rules readout", async () => {
    withPresets();
    renderWithApp(<CreateGamePanel />);
    const select = (await screen.findByLabelText(/rules/i)) as HTMLSelectElement;
    await waitFor(() => expect(select.value).toBe("default"));
    expect(screen.getByText(/4 players/i)).toBeInTheDocument();

    await userEvent.selectOptions(select, "blitz");
    expect(await screen.findByText(/2 players/i)).toBeInTheDocument();
    expect(screen.queryByText(/4 players/i)).not.toBeInTheDocument();
  });

  it("reads as a sensible non-choice, not an empty or a real dropdown, when only one preset exists", async () => {
    withPresets([DEFAULT_PRESET]);
    renderWithApp(<CreateGamePanel />);
    const select = (await screen.findByLabelText(/rules/i)) as HTMLSelectElement;
    await waitFor(() => expect(select.value).toBe("default"));
    // A single real option, and disabled rather than an interactive
    // dropdown that implies a choice which does not exist — the same
    // treatment the map picker already gives a single map.
    expect(within(select).getAllByRole("option")).toHaveLength(1);
    expect(select).toBeDisabled();
    expect(screen.getByText(/4 players/i)).toBeInTheDocument();
  });
});
