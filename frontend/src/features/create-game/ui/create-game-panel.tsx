import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { mapsQueryOptions, presetsQueryOptions } from "@/entities/game";
import { ApiFetchError } from "@/shared/api";
import { Banner, Button } from "@/shared/ui";
import { useCreateGame } from "../model/use-create-game";

/**
 * The right-hand panel of the lobby: pick a map, pick a ruleset, start a
 * game.
 *
 * `GET /api/presets` (Plan 7A Decision 1) is what makes the ruleset a real
 * choice — it is signed-in-only, not admin-only, and its `PresetSummary`
 * comes from the player contract (`generated/public.ts`), same as
 * `MapSummary`. When exactly one preset is active (today's shipped state:
 * the seeded "Default" preset and nothing else) the select is disabled
 * rather than hidden or left empty, the same treatment the map picker
 * already gives a single map — it still tells a player what ruleset they
 * are about to get, without implying a choice that does not exist.
 */
export function CreateGamePanel() {
  const maps = useQuery(mapsQueryOptions());
  const presets = useQuery(presetsQueryOptions());
  const createGame = useCreateGame();
  const [chosenMapId, setChosenMapId] = useState<string | null>(null);
  const [chosenPresetId, setChosenPresetId] = useState<string | null>(null);

  const options = maps.data ?? [];
  // The first map fetched is the default choice — there is exactly one
  // shipped map today, and a player should not have to pick before they
  // can create anything.
  const mapId = chosenMapId ?? options[0]?.map_id ?? null;
  const selectedMap = options.find((map) => map.map_id === mapId) ?? null;

  const presetOptions = presets.data ?? [];
  // Default to the preset the server flags `is_default`. The backend
  // enforces "never zero, at most one default" as an invariant (retiring or
  // un-defaulting the last default is refused with 409 `default_preset`),
  // so the `find` below should always succeed once any preset has loaded —
  // the fallback to the first preset is defensive, not an expected path.
  const presetId =
    chosenPresetId ??
    presetOptions.find((preset) => preset.is_default)?.id ??
    presetOptions[0]?.id ??
    null;
  const selectedPreset = presetOptions.find((preset) => preset.id === presetId) ?? null;

  return (
    <section className="flex w-80 shrink-0 flex-col gap-5 border border-line bg-panel p-6">
      <h2 className="font-display text-2xl tracking-wider text-gold">NEW GAME</h2>

      {createGame.error instanceof ApiFetchError && (
        // `exactOptionalPropertyTypes` forbids an optional `string` prop
        // written as `undefined`, so the prop is spread in only when there
        // is a real server code — same pattern as sign-in and redeem.
        <Banner
          tone="bad"
          {...(createGame.error.code !== null ? { code: createGame.error.code } : {})}
        >
          {createGame.error.message}
        </Banner>
      )}

      <div className="flex flex-col gap-2">
        <label
          htmlFor="create-game-map"
          className="text-[10px] font-semibold tracking-[0.14em] text-ink-dim"
        >
          MAP
        </label>
        <select
          id="create-game-map"
          value={mapId ?? ""}
          onChange={(event) => setChosenMapId(event.target.value)}
          disabled={options.length === 0}
          className="border-2 border-line bg-raised px-4 py-3 text-[15px] font-medium text-ink outline-none focus:border-gold disabled:text-ink-faint"
        >
          {options.length === 0 && <option value="">No maps available</option>}
          {options.map((map) => (
            <option key={map.map_id} value={map.map_id}>
              {map.map_id} — {map.region_count} regions
            </option>
          ))}
        </select>
      </div>

      <div className="flex flex-col gap-2 border-t border-line pt-4">
        <label
          htmlFor="create-game-preset"
          className="text-[10px] font-semibold tracking-[0.14em] text-ink-dim"
        >
          RULES
        </label>
        <select
          id="create-game-preset"
          value={presetId ?? ""}
          onChange={(event) => setChosenPresetId(event.target.value)}
          disabled={presetOptions.length <= 1}
          className="border-2 border-line bg-raised px-4 py-3 text-[15px] font-medium text-ink outline-none focus:border-gold disabled:text-ink-faint"
        >
          {presetOptions.length === 0 && <option value="">No rulesets available</option>}
          {presetOptions.map((preset) => (
            <option key={preset.id} value={preset.id}>
              {preset.name}
            </option>
          ))}
        </select>
        {selectedPreset !== null && (
          <p className="text-[13px] text-ink-dim">
            {selectedPreset.rules.player_count} players · {selectedPreset.rules.battle_rounds}{" "}
            battle rounds
          </p>
        )}
        {selectedMap !== null && (
          <p className="text-[13px] text-ink-dim">{selectedMap.region_count} regions to claim.</p>
        )}
      </div>

      <Button
        onClick={() => {
          if (mapId === null || presetId === null) return;
          createGame.mutate({ map_id: mapId, preset_id: presetId });
        }}
        disabled={mapId === null || presetId === null || createGame.isPending}
      >
        {createGame.isPending ? "Creating…" : "Create game"}
      </Button>
    </section>
  );
}
