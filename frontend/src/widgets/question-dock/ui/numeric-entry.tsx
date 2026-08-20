import { useState } from "react";
import type { QuestionResolvedEvent, SubmittedValue } from "@/shared/api";
import { Button, Field } from "@/shared/ui";

// A light shape check only — "is this typeable as a decimal" — never a
// parse. The value that reaches `onSubmit` is exactly what was typed,
// trimmed; nothing here ever runs it through `Number(...)` or `parseFloat`,
// because that is precisely how `Decimal("0.1")` stops round-tripping
// (§8.7, and `useSubmitAnswer`'s own doc comment).
const LOOKS_LIKE_A_DECIMAL = /^-?\d+(\.\d+)?$/;

/**
 * The numeric half of the dock. Same five states as `ChoiceList`, on one
 * field instead of a grid: untouched/pointed-at are the `Field`'s own
 * `:focus` styling, yours-and-sent is `yourAnswer !== null`, shut is
 * `disabled`, and revealed-correct is `resolved !== null` — shown as
 * `resolved.correct_value`, still a string, never parsed.
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
  const valid = LOOKS_LIKE_A_DECIMAL.test(trimmed);

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
