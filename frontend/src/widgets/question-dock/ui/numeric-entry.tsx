import { useState } from "react";
import type { QuestionResolvedEvent, SubmittedValue } from "@/shared/api";
import { Button, Field } from "@/shared/ui";

// The wire's own cap: `NumericAnswerPayload._decimal`
// (`backend/src/triviador/api/schemas/ws.py`) is `max_length=40`. Enabling
// Submit for a 41st character costs a round trip and a confusing rejection
// for something knowable locally — the asymmetric half of this grammar
// worth guarding eagerly, unlike being *stricter* than the server, which
// only costs a keystroke the player can see refused.
const MAX_LENGTH = 40;

// Mirrors what the server actually accepts — `_decimal` runs `Decimal(value)`
// and refuses only a result that is not finite. `Decimal` accepts a digit
// run on *either* side of the point as long as the other side has one too
// (`.5` and `5.` both parse; a bare `.` does not, because then neither side
// has a digit), plus an optional sign and an optional exponent — so `1e3`,
// `1E-3`, `.5` and `5.` are all legal decimal strings on the wire, and a
// client rule that rejects any of them is the client inventing a rule the
// server does not have. `Number.isFinite(Number(...))` on top of the shape
// check exists only to catch a shape that parses but overflows (`1e400`),
// the one case the shape alone cannot rule out. `Number(...)`'s result is
// discarded either way — the string that reaches `onSubmit` is exactly what
// was typed, trimmed, never the parsed number, because that is precisely
// how `Decimal("0.1")` stops round-tripping (§8.7, and `useSubmitAnswer`'s
// own doc comment).
//
// One deliberate exception, left rejected on purpose: `Decimal("1_000")`
// parses on the backend because Python accepts `_` as a digit-group
// separator in numeric literals generally — a quirk of the parser the
// contract happens to be written in, not an input format any player types.
// This grammar does not accept `_`; that refusal is an intentional input
// aid, not a gap this client failed to close, and the visible reason below
// tells the player what to type instead of leaving them guessing why an
// underscore didn't work.
const DECIMAL_SHAPE = /^[+-]?(\d+(\.\d*)?|\.\d+)([eE][+-]?\d+)?$/;

function isValidNumericAnswer(trimmed: string): boolean {
  return (
    trimmed.length > 0 &&
    trimmed.length <= MAX_LENGTH &&
    DECIMAL_SHAPE.test(trimmed) &&
    Number.isFinite(Number(trimmed))
  );
}

/**
 * The numeric half of the dock. Same five states as `ChoiceList`, on one
 * field instead of a grid: untouched/pointed-at are the `Field`'s own
 * `:focus` styling, yours-and-sent is `yourAnswer !== null`, shut is
 * `disabled`, and revealed-correct is `resolved !== null` — shown as
 * `resolved.correct_value`, still a string, never parsed.
 *
 * Submit is disabled both externally (`disabled`, the dock's four reasons —
 * expired / already answered / sending / not your turn) and locally (the
 * typed value isn't a legal decimal string yet, or is over the wire's
 * 40-character cap). Silently disabling for the second reason and saying
 * nothing would be the same mistake this dock's `reason` paragraph exists
 * to avoid one level up: `Field`'s own `error` carries the local reason, so
 * a malformed or over-length value is never a dead button with no
 * explanation — but only once the player has actually typed something.
 * `touched` (set the first time `onChange` fires) gates that error: an
 * untouched field is always invalid too (`isValidNumericAnswer` requires a
 * non-empty string), and showing the error before a single keystroke would
 * paint every numeric question red, `aria-invalid`, on open — and `Field`
 * hides `hint` whenever `error` is set, so that would hide the unit behind
 * a message about input nobody has entered yet. `maxLength` on the input
 * itself keeps the over-length case from being typeable at all, rather than
 * only refusing it after the fact.
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
  const [touched, setTouched] = useState(false);
  const trimmed = value.trim();
  const valid = isValidNumericAnswer(trimmed);
  const showError = touched && !valid;

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
          onChange={(event) => {
            setTouched(true);
            setValue(event.target.value);
          }}
          disabled={disabled}
          inputMode="decimal"
          autoComplete="off"
          maxLength={MAX_LENGTH}
          hint={unit ?? undefined}
          error={showError ? "Enter a number the server can read — e.g. 12, -3.5, 1e3." : undefined}
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
