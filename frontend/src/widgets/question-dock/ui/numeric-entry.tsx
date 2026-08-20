import { useState } from "react";
import type { QuestionResolvedEvent, SubmittedValue } from "@/shared/api";
import { Button, Field } from "@/shared/ui";

// Mirrors what the server actually accepts — `NumericAnswerPayload._decimal`
// in `backend/src/triviador/api/schemas/ws.py` runs `Decimal(value)` and
// refuses only a result that is not finite. That is a much wider grammar
// than "digits and a dot": optional sign, digits, an optional fractional
// part, an optional exponent — so `1e3` and `1E-3` are legal decimal
// strings on the wire, and a client rule that rejects them is the client
// inventing a rule the server does not have. The shape check below matches
// that grammar; `Number.isFinite(Number(...))` on top of it exists only to
// catch a shape that parses but overflows (`1e400`), the one case the shape
// alone cannot rule out. `Number(...)`'s result is discarded either way —
// the string that reaches `onSubmit` is exactly what was typed, trimmed,
// never the parsed number, because that is precisely how `Decimal("0.1")`
// stops round-tripping (§8.7, and `useSubmitAnswer`'s own doc comment).
const DECIMAL_SHAPE = /^[+-]?\d+(\.\d+)?([eE][+-]?\d+)?$/;

function isValidNumericAnswer(trimmed: string): boolean {
  return DECIMAL_SHAPE.test(trimmed) && Number.isFinite(Number(trimmed));
}

/**
 * The numeric half of the dock. Same five states as `ChoiceList`, on one
 * field instead of a grid: untouched/pointed-at are the `Field`'s own
 * `:focus` styling, yours-and-sent is `yourAnswer !== null`, shut is
 * `disabled`, and revealed-correct is `resolved !== null` — shown as
 * `resolved.correct_value`, still a string, never parsed.
 *
 * Submit is disabled both externally (`disabled`, the dock's three reasons
 * — expired / already answered / sending) and locally (the typed value
 * isn't a legal decimal string yet). Silently disabling for the second
 * reason and saying nothing would be the same mistake this dock's `reason`
 * paragraph exists to avoid one level up: `Field`'s own `error` carries the
 * local reason, so a malformed value is never a dead button with no
 * explanation.
 */
export function NumericEntry({
  unit,
  yourAnswer,
  resolved,
  disabled,
  onSubmit,
}: {
  unit: string | null;
  yourAnswer: SubmittedValue | null;
  resolved: QuestionResolvedEvent | null;
  disabled: boolean;
  onSubmit: (value: string) => void;
}) {
  const [value, setValue] = useState("");
  const trimmed = value.trim();
  const valid = isValidNumericAnswer(trimmed);

  return (
    <form
      className="flex flex-wrap items-end gap-3"
      onSubmit={(event) => {
        event.preventDefault();
        if (!disabled && valid) onSubmit(trimmed);
      }}
    >
      <div className="w-40">
        <Field
          label="Your answer"
          value={value}
          onChange={(event) => setValue(event.target.value)}
          disabled={disabled}
          inputMode="decimal"
          autoComplete="off"
          hint={unit ?? undefined}
          error={valid ? undefined : "Enter a number the server can read — e.g. 12, -3.5, 1e3."}
        />
      </div>
      <Button type="submit" disabled={disabled || !valid}>
        Submit
      </Button>
      {yourAnswer !== null && yourAnswer.value !== null && (
        <span className="text-[13px] text-ink-dim">You answered {yourAnswer.value}</span>
      )}
      {resolved !== null && resolved.correct_value !== null && (
        <span data-testid="numeric-correct" className="text-[13px] font-medium text-good">
          Correct: {resolved.correct_value}
          {unit !== null ? ` ${unit}` : ""}
        </span>
      )}
    </form>
  );
}
