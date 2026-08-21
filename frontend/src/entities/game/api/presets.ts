import { queryOptions } from "@tanstack/react-query";
import { z } from "zod";
import { apiFetch, presetSummarySchema } from "@/shared/api";
import { presetsKey } from "../model/keys";

/**
 * The rules choice on the create-game panel. `GET /api/presets` (Plan 7A
 * Decision 1) is signed-in-only but explicitly not admin-only — Spec 1B §6.1
 * lists presets under `/api/admin`, and this route is the one deliberate
 * exception, added so `POST /api/games`'s `preset_id` is a choice a lobby
 * can actually offer.
 *
 * `PresetSummary`/`RulesView` live in the *player* contract
 * (`generated/public.ts`, re-exported through `@/shared/api`), not
 * `generated/admin.ts`. Importing from the admin module here — even for a
 * type that happens to have the right shape — would put admin-only schema
 * construction in every player's bundle, which `pnpm check:bundle`'s
 * `assert-admin-split.mjs` exists to catch.
 */
export function presetsQueryOptions() {
  return queryOptions({
    queryKey: presetsKey(),
    queryFn: () => apiFetch("/api/presets", z.array(presetSummarySchema)),
  });
}
