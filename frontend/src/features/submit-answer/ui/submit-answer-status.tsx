import type { CommandFailure } from "@/shared/api";
import { Banner } from "@/shared/ui";

/**
 * The one place a rejected `submit_answer` is shown — `not_your_turn`,
 * `already_answered`, `answer_kind_mismatch`. Always the server's own code
 * and message (decision 2); the dock never re-derives a reason.
 */
export function SubmitAnswerStatus({ failure }: { failure: CommandFailure | null }) {
  if (failure === null) return null;
  return (
    <Banner tone="bad" code={failure.code}>
      {failure.message}
    </Banner>
  );
}
