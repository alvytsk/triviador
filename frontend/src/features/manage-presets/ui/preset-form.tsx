import { useMutation, useQueryClient } from "@tanstack/react-query";
import type { ChangeEvent, FormEvent } from "react";
import { useState } from "react";
import { adminKeys, createPreset, updatePreset } from "@/entities/admin";
import { ApiFetchError } from "@/shared/api";
import type { PresetDetail, PresetWriteRequest, RulesView } from "@/shared/api/generated/admin";
import { adminErrorMessage } from "@/shared/lib/admin-errors";
import { Banner, Button, Field } from "@/shared/ui";

export interface PresetFormProps {
  /** Omitted -> create. Present -> edit that preset, retired or not. */
  preset?: PresetDetail;
  onSaved: (preset: PresetDetail) => void;
}

/**
 * A sensible starting point for a brand-new preset, not a policy — lifted
 * from the backend's own `DEFAULT_RULES`
 * (`triviador/domain/game/rules.py`) so a fresh preset here starts out
 * looking like the seeded default one, which an admin can then edit
 * freely. Nothing here is enforced; it is only ever the initial state of
 * an editable field.
 */
const STARTING_RULES: RulesView = {
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

function claimsToText(claims: readonly number[]): string {
  return claims.join(", ");
}

function textToClaims(text: string): number[] {
  return text
    .split(",")
    .map((entry) => entry.trim())
    .filter((entry) => entry.length > 0)
    .map((entry) => {
      const n = Number(entry);
      return Number.isFinite(n) ? n : 0;
    });
}

/**
 * §10.6's CRUD form over `GameRules`. Deliberately carries NO field-level
 * bounds beyond the generated schema's plain `int` types (`rulesViewSchema`
 * — every rules field is `z.number().int()`, nothing narrower):
 * `validate_rules` in `triviador/domain/game/rules.py` is the single
 * definition of a legal ruleset, and the admin route
 * (`api/http/admin/presets.py`'s `_rules` helper, whose own docstring says
 * the same thing) calls it rather than re-encoding its bounds in a
 * Pydantic model. A client that restated "player_count must be 2..8" or
 * "timeout must be 5000..60000" here would be a second copy of that rule
 * — and the copy is the one that drifts. So this form sends whatever the
 * admin typed and renders whatever `validate_rules` says back through the
 * 422, never a guess of its own. (No `<input min max>` either, for the
 * same reason — that would restate the same bounds one layer down.)
 *
 * Plain `useState` rather than TanStack Form: unlike the question editor,
 * nothing here needs cross-field derivation or a kind switch, and
 * `claims_by_rank` needs its own text<->array parsing that would fight a
 * schema-typed form's input/output distinction for no benefit.
 */
export function PresetForm({ preset, onSaved }: PresetFormProps) {
  const queryClient = useQueryClient();
  const startingRules = preset?.rules ?? STARTING_RULES;

  const [name, setName] = useState(preset?.name ?? "");
  const [isDefault, setIsDefault] = useState(preset?.is_default ?? false);
  const [playerCount, setPlayerCount] = useState(startingRules.player_count);
  const [expansionRounds, setExpansionRounds] = useState(startingRules.expansion_rounds);
  const [battleRounds, setBattleRounds] = useState(startingRules.battle_rounds);
  const [baseHp, setBaseHp] = useState(startingRules.base_hp);
  const [answerTimeoutMs, setAnswerTimeoutMs] = useState(startingRules.answer_timeout_ms);
  const [pickTimeoutMs, setPickTimeoutMs] = useState(startingRules.pick_timeout_ms);
  const [warmupMs, setWarmupMs] = useState(startingRules.warmup_ms);
  const [claimsText, setClaimsText] = useState(claimsToText(startingRules.claims_by_rank));
  const [ptsBase, setPtsBase] = useState(startingRules.pts_base);
  const [ptsTerritory, setPtsTerritory] = useState(startingRules.pts_territory);
  const [ptsConquered, setPtsConquered] = useState(startingRules.pts_conquered);
  const [ptsDefense, setPtsDefense] = useState(startingRules.pts_defense);

  const mutation = useMutation({
    mutationFn: (body: PresetWriteRequest) =>
      preset === undefined ? createPreset(body) : updatePreset(preset.id, body),
    onSuccess: (saved) => {
      queryClient.invalidateQueries({ queryKey: adminKeys.presets() });
      if (preset !== undefined) {
        queryClient.invalidateQueries({ queryKey: adminKeys.coverage(preset.id) });
      }
      onSaved(saved);
    },
  });
  const error = mutation.error instanceof ApiFetchError ? mutation.error : null;

  function handleSubmit(event: FormEvent) {
    event.preventDefault();
    const body: PresetWriteRequest = {
      name,
      is_default: isDefault,
      rules: {
        player_count: playerCount,
        expansion_rounds: expansionRounds,
        battle_rounds: battleRounds,
        base_hp: baseHp,
        answer_timeout_ms: answerTimeoutMs,
        pick_timeout_ms: pickTimeoutMs,
        warmup_ms: warmupMs,
        claims_by_rank: textToClaims(claimsText),
        pts_base: ptsBase,
        pts_territory: ptsTerritory,
        pts_conquered: ptsConquered,
        pts_defense: ptsDefense,
      },
    };
    mutation.mutate(body);
  }

  function numberField(setter: (n: number) => void, current: number) {
    return (event: ChangeEvent<HTMLInputElement>) => {
      const next = event.target.valueAsNumber;
      setter(Number.isNaN(next) ? current : next);
    };
  }

  return (
    <form className="flex max-w-2xl flex-col gap-6" onSubmit={handleSubmit}>
      {/* §10.6: true because `games.rules` (backend/src/triviador/db/models/games.py)
       *  is a frozen copy taken at creation, not a live reference to this
       *  preset's row — see that model's own comment for why. */}
      <Banner tone="quiet">
        Editing a preset does not affect any game already running — a running game holds a frozen
        copy of the rules it started with, taken when it was created.
      </Banner>

      {error !== null && (
        <Banner tone="bad" {...(error.code !== null ? { code: error.code } : {})}>
          {adminErrorMessage(error.code ?? "validation_failed", error.message)}
        </Banner>
      )}

      <Field label="Name" value={name} onChange={(event) => setName(event.target.value)} />

      <label className="flex items-center gap-2 text-[13px] font-medium text-ink">
        <input
          type="checkbox"
          checked={isDefault}
          onChange={(event) => setIsDefault(event.target.checked)}
        />
        Default preset
      </label>

      <div className="grid grid-cols-2 gap-4">
        <Field
          label="Player count"
          type="number"
          value={playerCount}
          onChange={numberField(setPlayerCount, playerCount)}
        />
        <Field
          label="Expansion rounds"
          type="number"
          value={expansionRounds}
          onChange={numberField(setExpansionRounds, expansionRounds)}
        />
        <Field
          label="Battle rounds"
          type="number"
          value={battleRounds}
          onChange={numberField(setBattleRounds, battleRounds)}
        />
        <Field
          label="Base HP"
          type="number"
          value={baseHp}
          onChange={numberField(setBaseHp, baseHp)}
        />
        <Field
          label="Answer timeout (ms)"
          type="number"
          value={answerTimeoutMs}
          onChange={numberField(setAnswerTimeoutMs, answerTimeoutMs)}
        />
        <Field
          label="Pick timeout (ms)"
          type="number"
          value={pickTimeoutMs}
          onChange={numberField(setPickTimeoutMs, pickTimeoutMs)}
        />
        <Field
          label="Warmup (ms)"
          type="number"
          value={warmupMs}
          onChange={numberField(setWarmupMs, warmupMs)}
        />
        <Field
          label="Points: base"
          type="number"
          value={ptsBase}
          onChange={numberField(setPtsBase, ptsBase)}
        />
        <Field
          label="Points: territory"
          type="number"
          value={ptsTerritory}
          onChange={numberField(setPtsTerritory, ptsTerritory)}
        />
        <Field
          label="Points: conquered"
          type="number"
          value={ptsConquered}
          onChange={numberField(setPtsConquered, ptsConquered)}
        />
        <Field
          label="Points: defense"
          type="number"
          value={ptsDefense}
          onChange={numberField(setPtsDefense, ptsDefense)}
        />
      </div>

      <Field
        label="Claims by rank"
        value={claimsText}
        onChange={(event) => setClaimsText(event.target.value)}
        hint="Comma-separated, one entry per player rank — e.g. 2, 1, 0. Must have exactly
          player_count entries; the server says so if it doesn't."
      />

      <Button type="submit" disabled={mutation.isPending}>
        {mutation.isPending ? "Saving…" : preset === undefined ? "Create preset" : "Save changes"}
      </Button>
    </form>
  );
}
