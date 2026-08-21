import type { ChoiceWrite } from "@/shared/api/generated/admin";

/**
 * Exactly four rows, always — the array passed in is always length four
 * (`blankChoices()`/the loaded question's four choices), and nothing here
 * adds or removes one. The radio group is what makes "two correct"
 * physically unreachable in the UI: clicking one *is* unmarking every
 * other one, native browser semantics doing the same job
 * `markCorrect` does in state.
 */
export function ChoiceEditor({
  choices,
  onTextChange,
  onMarkCorrect,
}: {
  choices: ChoiceWrite[];
  onTextChange: (index: number, text: string) => void;
  onMarkCorrect: (index: number) => void;
}) {
  return (
    <div className="flex flex-col gap-3">
      <span className="text-[10px] font-semibold uppercase tracking-[0.14em] text-ink-dim">
        Choices
      </span>
      {choices.map((choice, index) => (
        // biome-ignore lint/suspicious/noArrayIndexKey: a fixed four-item answer grid, never reordered — the index *is* the choice's identity, same as game-row.tsx's seat pips.
        <div key={`choice-${index}`} className="flex items-center gap-3">
          <input
            type="radio"
            name="correct-choice"
            aria-label={`Choice ${index + 1} correct`}
            checked={choice.is_correct}
            onChange={() => onMarkCorrect(index)}
            className="h-4 w-4 accent-gold"
          />
          <input
            aria-label={`Choice ${index + 1}`}
            value={choice.text}
            onChange={(event) => onTextChange(index, event.target.value)}
            maxLength={200}
            className="flex-1 bg-raised border-2 border-line px-4 py-2 text-[14px] font-medium text-ink outline-none focus:border-gold"
          />
        </div>
      ))}
    </div>
  );
}
