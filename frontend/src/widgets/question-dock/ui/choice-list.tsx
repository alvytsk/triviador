import type { ClientChoice, QuestionResolvedEvent, SubmittedValue } from "@/shared/api";
import { cn } from "@/shared/lib";

/**
 * The multiple-choice half of the dock's five states (design canvas's system
 * artboard): untouched and pointed-at are plain CSS (`:hover`/`:focus`
 * through `hover:`/`focus-visible:`); yours-and-sent is `yourAnswer.idx`;
 * shut is the caller's `disabled`; and revealed-correct is `resolved`,
 * *never* derived from `state` — `correct_choice_index` exists only on the
 * `question_resolved` narration event, because the pre-resolution DTO has
 * nowhere to put it (§8.7). Before that event arrives `resolved` is `null`
 * and no `<button>` here renders `data-testid="choice-correct"` or
 * `"choice-incorrect"` at all — not hidden, not styled away, structurally
 * absent — which is what makes "nothing is revealed before the server
 * reveals it" a guarantee a test can assert rather than a rule a reviewer
 * has to trust.
 */
export function ChoiceList({
  choices,
  yourAnswer,
  resolved,
  disabled,
  onPick,
}: {
  choices: readonly ClientChoice[];
  yourAnswer: SubmittedValue | null;
  resolved: QuestionResolvedEvent | null;
  disabled: boolean;
  onPick: (idx: number) => void;
}) {
  return (
    <ul className="grid grid-cols-1 gap-2 sm:grid-cols-2">
      {choices.map((choice) => {
        const isYours = yourAnswer !== null && yourAnswer.idx === choice.idx;
        const isCorrect = resolved !== null && resolved.correct_choice_index === choice.idx;
        const isWrong = resolved !== null && isYours && !isCorrect;

        return (
          <li key={choice.idx}>
            <button
              type="button"
              disabled={disabled}
              aria-pressed={isYours}
              onClick={() => onPick(choice.idx)}
              className={cn(
                "flex w-full items-center gap-2 border-2 px-4 py-3 text-left text-[14px] font-medium",
                "transition-colors disabled:cursor-not-allowed",
                isCorrect
                  ? "border-good text-good"
                  : isWrong
                    ? "border-bad text-bad"
                    : isYours
                      ? "border-gold text-ink"
                      : "border-line text-ink hover:border-line-strong disabled:hover:border-line",
              )}
            >
              <span>{choice.text}</span>
              {isCorrect && (
                <span
                  data-testid="choice-correct"
                  className="ml-auto text-[10px] font-semibold uppercase tracking-[0.14em] text-good"
                >
                  Correct
                </span>
              )}
              {isWrong && (
                <span
                  data-testid="choice-incorrect"
                  className="ml-auto text-[10px] font-semibold uppercase tracking-[0.14em] text-bad"
                >
                  Your answer
                </span>
              )}
            </button>
          </li>
        );
      })}
    </ul>
  );
}
