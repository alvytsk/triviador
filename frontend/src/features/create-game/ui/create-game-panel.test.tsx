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
  rules: rules({ player_count: 2, expansion_rounds: 1, battle_rounds: 1 }),
};

// `is_default` preset (Default) deliberately NOT first: the real backend
// orders `ORDER BY RulePreset.name` (`repos/presets.py`), alphabetically,
// so with "Blitz" and "Default" it returns Blitz first. A fixture that put
// Default first would make the "defaults to is_default" test below pass
// even with the `find(is_default)` fallback deleted from production —
// array-order coincidentally reaching the same answer. This order is what
// makes that assertion load-bearing (see the test's own comment).
function withPresets(presets: unknown[] = [BLITZ_PRESET, DEFAULT_PRESET]) {
  server.use(
    http.get("/api/maps", () => HttpResponse.json([MAP])),
    http.get("/api/presets", () => HttpResponse.json(presets)),
  );
}

describe("CreateGamePanel", () => {
  // Load-bearing on the fixture's declaration order (see `withPresets`'s
  // comment): with Blitz listed FIRST, only the `find((preset) =>
  // preset.is_default)` fallback in `create-game-panel.tsx` can produce
  // "default" here — an array-order fallback would pick Blitz instead.
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
    // A match is two stages — Expansion, then Battle (Spec §3.1) —
    // reporting battle_rounds alone is only half the game's length.
    expect(screen.getByText(/3 expansion rounds/i)).toBeInTheDocument();

    await userEvent.selectOptions(select, "blitz");
    expect(await screen.findByText(/2 players/i)).toBeInTheDocument();
    expect(screen.getByText(/1 expansion rounds/i)).toBeInTheDocument();
    expect(screen.queryByText(/4 players/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/3 expansion rounds/i)).not.toBeInTheDocument();
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
