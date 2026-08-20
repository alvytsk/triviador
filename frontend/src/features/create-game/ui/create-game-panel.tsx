import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { mapsQueryOptions } from "@/entities/game";
import { ApiFetchError } from "@/shared/api";
import { Banner, Button } from "@/shared/ui";
import { useCreateGame } from "../model/use-create-game";

/**
 * The right-hand panel of the lobby: pick a map, see the default rules,
 * start a game.
 *
 * There is no preset select. `GET /api/presets` does not exist — the preset
 * repository is read-only and unexposed until Plan 7 — so this panel sends
 * `preset_id: null` and lets the server choose its default, and says so in
 * one line rather than rendering a select with a single hard-coded option,
 * which would imply a choice the player does not actually have.
 */
export function CreateGamePanel() {
  const maps = useQuery(mapsQueryOptions());
  const createGame = useCreateGame();
  const [chosenMapId, setChosenMapId] = useState<string | null>(null);

  const options = maps.data ?? [];
  // The first map fetched is the default choice — there is exactly one
  // shipped map today, and a player should not have to pick before they
  // can create anything.
  const mapId = chosenMapId ?? options[0]?.map_id ?? null;
  const selectedMap = options.find((map) => map.map_id === mapId) ?? null;

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

      <div className="flex flex-col gap-1 border-t border-line pt-4">
        <span className="text-[10px] font-semibold tracking-[0.14em] text-ink-dim">RULES</span>
        <p className="text-[13px] text-ink-dim">
          Default rules — presets are configurable from the admin screens.
        </p>
        {selectedMap !== null && (
          <p className="text-[13px] text-ink-dim">{selectedMap.region_count} regions to claim.</p>
        )}
      </div>

      <Button
        onClick={() => {
          if (mapId === null) return;
          createGame.mutate({ map_id: mapId, preset_id: null });
        }}
        disabled={mapId === null || createGame.isPending}
      >
        {createGame.isPending ? "Creating…" : "Create game"}
      </Button>
    </section>
  );
}
