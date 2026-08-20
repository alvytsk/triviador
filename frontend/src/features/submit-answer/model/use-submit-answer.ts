import { type AnswerPayload, useCommand } from "@/shared/api";

/**
 * Decision 10's shape for `submit_answer`, with the payload built from the
 * question's kind. `answerNumeric` never parses `value` — it stays a string
 * from the input to the wire, because JSON has one number type and it is a
 * float: `Decimal("0.1")` does not survive a round trip through `Number`,
 * which is exactly why `SubmittedValue.value` is a string in the contract.
 *
 * `payload` is typed against `AnswerPayload` (declared in
 * `shared/api/messages.ts` from the same two generated schemas the code
 * generator could not express as a union) rather than the `any` the
 * generator left `submitAnswerFrameSchema["payload"]` as — so a typo here is
 * a compile error, not a `validation_failed` with no `command_id` to
 * correlate it to.
 */
export function useSubmitAnswer(gameId: string, deadlineId: number | null) {
  const { send, pending, failure } = useCommand();

  const submit = (payload: AnswerPayload): void => {
    if (deadlineId === null) return;
    send((command_id) => ({
      type: "submit_answer",
      command_id,
      game_id: gameId,
      deadline_id: deadlineId,
      payload,
    }));
  };

  return {
    answerChoice: (idx: number) => submit({ kind: "choice", idx }),
    answerNumeric: (value: string) => submit({ kind: "numeric", value }),
    isSending: pending.size > 0,
    failure,
  };
}
