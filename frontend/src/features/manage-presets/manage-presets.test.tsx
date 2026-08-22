import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { HttpResponse, http } from "msw";
import { useState } from "react";
import { describe, expect, it, vi } from "vitest";
import type { PresetDetail } from "@/shared/api/generated/admin";
import { server } from "../../../testing/msw";
import { renderWithApp } from "../../../testing/render";
import { CoveragePanel } from "./ui/coverage-panel";
import { PresetForm } from "./ui/preset-form";
import { PresetList } from "./ui/preset-list";

const RULES = {
  player_count: 3,
  expansion_rounds: 4,
  battle_rounds: 4,
  base_hp: 3,
  answer_timeout_ms: 20_000,
  pick_timeout_ms: 15_000,
  warmup_ms: 5_000,
  claims_by_rank: [2, 1, 0],
  pts_base: 1000,
  pts_territory: 200,
  pts_conquered: 400,
  pts_defense: 100,
};

function preset(overrides: Partial<PresetDetail> = {}): PresetDetail {
  return {
    id: "p1",
    name: "Classic",
    is_default: true,
    is_active: true,
    rules: RULES,
    ...overrides,
  };
}

describe("PresetForm", () => {
  it("shows the server's validation messages rather than its own", async () => {
    // `validate_rules` (backend) is the single definition of a legal
    // ruleset. This form must not restate its bounds — it sends whatever
    // was typed and renders whatever the 422 says, verbatim.
    server.use(
      http.post("/api/admin/presets", () =>
        HttpResponse.json(
          {
            code: "validation_failed",
            message:
              "claims_by_rank must have exactly player_count entries; base_hp must be at least 1",
            details: null,
          },
          { status: 422 },
        ),
      ),
    );
    renderWithApp(<PresetForm onSaved={vi.fn()} />);

    await userEvent.click(screen.getByRole("button", { name: /create preset/i }));

    expect(
      await screen.findByText(
        /claims_by_rank must have exactly player_count entries; base_hp must be at least 1/,
      ),
    ).toBeInTheDocument();
  });

  it("says editing a preset does not affect running games", async () => {
    // §10.6, true because `games.rules` (backend/src/triviador/db/models/games.py)
    // holds a frozen copy taken at creation — editing this preset afterward
    // cannot reach a row already in progress.
    renderWithApp(<PresetForm preset={preset()} onSaved={vi.fn()} />);

    expect(screen.getByText(/does not affect.*running games?/i)).toBeInTheDocument();
  });

  it("renders default_preset's sentence for both refusals", async () => {
    // Clearing the current default -> 409 default_preset, one sentence.
    server.use(
      http.patch("/api/admin/presets/p1", () =>
        HttpResponse.json(
          {
            code: "default_preset",
            message:
              "this is the default preset; make another one default instead of clearing this one",
            details: null,
          },
          { status: 409 },
        ),
      ),
    );
    const clearing = renderWithApp(
      <PresetForm
        preset={preset({ id: "p1", is_default: true, is_active: true })}
        onSaved={vi.fn()}
      />,
    );
    await userEvent.click(within(clearing.container).getByRole("checkbox", { name: /default/i }));
    await userEvent.click(
      within(clearing.container).getByRole("button", { name: /save changes/i }),
    );
    expect(
      await screen.findByText(
        /this is the default preset; make another one default instead of clearing this one/,
      ),
    ).toBeInTheDocument();
    clearing.unmount();

    // Promoting a retired preset to default -> 409 default_preset, a
    // DIFFERENT sentence for the same code.
    server.use(
      http.patch("/api/admin/presets/p2", () =>
        HttpResponse.json(
          {
            code: "default_preset",
            message: "a retired preset cannot be the default; reactivate it first",
            details: null,
          },
          { status: 409 },
        ),
      ),
    );
    const promoting = renderWithApp(
      <PresetForm
        preset={preset({ id: "p2", name: "Retired one", is_default: false, is_active: false })}
        onSaved={vi.fn()}
      />,
    );
    await userEvent.click(within(promoting.container).getByRole("checkbox", { name: /default/i }));
    await userEvent.click(
      within(promoting.container).getByRole("button", { name: /save changes/i }),
    );
    expect(
      await screen.findByText(/a retired preset cannot be the default; reactivate it first/),
    ).toBeInTheDocument();
  });
});

describe("CoveragePanel", () => {
  it("renders coverage as need-vs-bank per kind, and says it is informative", async () => {
    server.use(
      http.get("/api/admin/presets/p1/coverage", () =>
        HttpResponse.json({
          required: { numeric: 17, multiple_choice: 12 },
          bank: { numeric: 34, multiple_choice: 9 },
          sufficient: false,
          informative: true,
        }),
      ),
    );
    renderWithApp(<CoveragePanel presetId="p1" />);

    const numericRow = (await screen.findByText(/numeric/i)).closest("tr");
    expect(numericRow).not.toBeNull();
    expect(within(numericRow as HTMLElement).getByText("17")).toBeInTheDocument();
    expect(within(numericRow as HTMLElement).getByText("34")).toBeInTheDocument();

    const mcRow = screen.getByText(/multiple choice/i).closest("tr");
    expect(mcRow).not.toBeNull();
    expect(within(mcRow as HTMLElement).getByText("12")).toBeInTheDocument();
    expect(within(mcRow as HTMLElement).getByText("9")).toBeInTheDocument();

    expect(screen.getByText(/informative, not authoritative/i)).toBeInTheDocument();
  });

  it("does not show the informative sentence when the server says it is not informative", async () => {
    // Anti-vacuous check: if the sentence were hardcoded unconditionally
    // rather than driven by the `informative` field, this render would
    // still show it and the assertion below would fail to catch that.
    server.use(
      http.get("/api/admin/presets/p1/coverage", () =>
        HttpResponse.json({
          required: { numeric: 5, multiple_choice: 5 },
          bank: { numeric: 5, multiple_choice: 5 },
          sufficient: true,
          informative: false,
        }),
      ),
    );
    renderWithApp(<CoveragePanel presetId="p1" />);

    await screen.findByText(/numeric/i);
    expect(screen.queryByText(/informative, not authoritative/i)).not.toBeInTheDocument();
  });
});

describe("PresetList", () => {
  it("shows a retired preset as retired, and can open it", async () => {
    // Plan 7A's `get_including_retired` exists precisely so this screen
    // can show one and let an admin open (read) it — there is no
    // reactivation route, so this is read access, not a way back.
    function Host() {
      const [selectedId, setSelectedId] = useState<string | null>(null);
      const presets = [
        preset({ id: "p1", name: "Classic", is_active: true, is_default: true }),
        preset({ id: "p2", name: "Old rules", is_active: false, is_default: false }),
      ];
      const selected = presets.find((item) => item.id === selectedId);
      return (
        <>
          <PresetList presets={presets} selectedId={selectedId} onSelect={setSelectedId} />
          {selected !== undefined && <PresetForm preset={selected} onSaved={vi.fn()} />}
        </>
      );
    }
    renderWithApp(<Host />);

    expect(screen.getByText("Default")).toBeInTheDocument();
    expect(screen.getByText("Retired")).toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: /open old rules/i }));

    expect(screen.getByDisplayValue("Old rules")).toBeInTheDocument();
  });

  it("offers a retire control for an active preset but not for an already-retired one", () => {
    // Same reasoning as `UserTable`'s `DeactivateControl`: a preset that
    // is already retired has nothing left to retire.
    const presets = [
      preset({ id: "p1", name: "Classic", is_active: true, is_default: false }),
      preset({ id: "p2", name: "Old rules", is_active: false, is_default: false }),
    ];
    renderWithApp(<PresetList presets={presets} selectedId={null} onSelect={vi.fn()} />);

    const activeRow = screen.getByRole("button", { name: /open classic/i }).closest("tr");
    const retiredRow = screen.getByRole("button", { name: /open old rules/i }).closest("tr");
    expect(activeRow).not.toBeNull();
    expect(retiredRow).not.toBeNull();

    expect(
      within(activeRow as HTMLElement).getByRole("button", { name: /^retire$/i }),
    ).toBeInTheDocument();
    expect(
      within(retiredRow as HTMLElement).queryByRole("button", { name: /^retire$/i }),
    ).not.toBeInTheDocument();
  });

  it("says retiring cannot be undone before it happens", async () => {
    // §6.1's soft delete is one-way — no reactivation route — so the
    // confirmation copy must say so before the second click sends
    // anything, same two-step shape as `DeactivateControl`.
    const presets = [preset({ id: "p1", name: "Classic", is_active: true, is_default: false })];
    renderWithApp(<PresetList presets={presets} selectedId={null} onSelect={vi.fn()} />);

    await userEvent.click(screen.getByRole("button", { name: /^retire$/i }));

    expect(screen.getByText(/cannot be undone/i)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /^retire$/i })).not.toBeInTheDocument();
  });

  it("renders default_preset's third sentence when retiring the current default", async () => {
    // The DELETE path's own refusal (`DeactivateOutcome.IS_DEFAULT` in
    // `api/http/admin/presets.py`) is a THIRD distinct message for
    // `default_preset` — different from both of the PATCH refusals this
    // file's `PresetForm` tests already cover.
    server.use(
      http.delete("/api/admin/presets/p1", () =>
        HttpResponse.json(
          {
            code: "default_preset",
            message: "this is the default preset; make another one default first",
            details: null,
          },
          { status: 409 },
        ),
      ),
    );
    const presets = [preset({ id: "p1", name: "Classic", is_active: true, is_default: true })];
    renderWithApp(<PresetList presets={presets} selectedId={null} onSelect={vi.fn()} />);

    await userEvent.click(screen.getByRole("button", { name: /^retire$/i }));
    await userEvent.click(screen.getByRole("button", { name: /confirm retire/i }));

    expect(
      await screen.findByText(/this is the default preset; make another one default first/),
    ).toBeInTheDocument();
  });
});
