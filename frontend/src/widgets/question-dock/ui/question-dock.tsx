import { deadlineIdOf, deadlineOf, yourAnswer as yourAnswerOf } from "@/entities/game";
import { questionOf } from "@/entities/question";
import { SubmitAnswerStatus, useSubmitAnswer } from "@/features/submit-answer";
import { type ClientGameState, type QuestionResolvedEvent, useSocket } from "@/shared/api";
import { useDeadline } from "@/shared/lib";
import { Chip, TimerBar } from "@/shared/ui";
import { ChoiceList } from "./choice-list";
import { NumericEntry } from "./numeric-entry";

/**
 * §9.5's dock, filled: category and difficulty, the prompt in Barlow 600,
 * then `<ChoiceList>` or `<NumericEntry>` by `question.kind`, then
 * `<TimerBar>`.
 *
 * `resolved` is the one piece of state this component cannot derive from
 * `state` itself — `correct_choice_index`/`correct_value` live only on the
 * `question_resolved` narration event (§8.7), and `useNarration` is
 * `app/socket-provider.tsx`'s, which a widget may not import (steiger's
 * `fsd/forbidden-imports`, the same wall `use-command.ts` hit). So the
 * subscription lives at `app/routes/_authed.games.$gameId.tsx` — the one
 * place both it and `<GamePage>` are importable together — and the latest
 * `question_resolved` event is threaded down as this prop, the same pattern
 * Task 12 used for `gameQueryOptions`'s result. It defaults to `null` so
 * this component is fully testable on its own, without that route.
 */
export function QuestionDock({
  state,
  resolved = null,
}: {
  state: ClientGameState;
  resolved?: QuestionResolvedEvent | null;
}) {
  const question = questionOf(state);
  const deadlineId = deadlineIdOf(state);
  const { offsetMs } = useSocket();
  const { expired } = useDeadline(deadlineOf(state), offsetMs);
  const answer = useSubmitAnswer(state.game_id, deadlineId);
  const already = yourAnswerOf(state);

  if (question === null) return null;

  // Three separate reasons input is disabled, and the dock says which
  // (never a generic "can't answer").
  const reason = expired
    ? "Time is up."
    : already !== null
      ? "Answer sent."
      : answer.isSending
        ? "Sending…"
        : null;
  const disabled = reason !== null;

  return (
    <div className="flex grow flex-col justify-center gap-4 border-t border-line bg-panel px-6 py-4">
      <div className="flex items-center justify-between gap-4">
        <div className="flex items-center gap-2">
          <Chip>{question.category}</Chip>
          <Chip>{question.difficulty}</Chip>
        </div>
        <TimerBar deadlineAt={deadlineOf(state)} />
      </div>

      <p className="font-sans text-xl font-semibold text-ink">{question.prompt}</p>

      <SubmitAnswerStatus failure={answer.failure} />
      {reason !== null && <p className="text-[11px] font-medium text-ink-dim">{reason}</p>}

      {question.kind === "multiple_choice" ? (
        <ChoiceList
          choices={question.choices ?? []}
          yourAnswer={already}
          resolved={resolved}
          disabled={disabled}
          onPick={answer.answerChoice}
        />
      ) : (
        <NumericEntry
          unit={question.unit}
          yourAnswer={already}
          resolved={resolved}
          disabled={disabled}
          onSubmit={answer.answerNumeric}
        />
      )}
    </div>
  );
}
