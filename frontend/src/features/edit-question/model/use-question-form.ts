import { useForm } from "@tanstack/react-form";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import type { z } from "zod";
import { adminKeys, createQuestion, updateQuestion } from "@/entities/admin";
import { ApiFetchError } from "@/shared/api";
import {
  type CategoryView,
  type ChoiceWrite,
  type QuestionDetail,
  type QuestionWriteRequest,
  questionWriteRequestSchema,
} from "@/shared/api/generated/admin";

/**
 * `z.input`, not the `QuestionWriteRequest` output type: four fields
 * (`choices`, `media_asset_id`, `numeric_answer`, `unit`) carry
 * `.default(null)` in the generated schema, which makes them optional
 * (`T | undefined`, not just `T | null`) on the schema's *input* side.
 * TanStack Form's `validators.onSubmit` requires the validator's Standard
 * Schema input type to match `TFormData` exactly, and `TFormData` is
 * inferred from `defaultValues` — so the form's value type has to be the
 * schema's input type, not its output type, or `tsc` rejects the
 * validator wiring outright. This form always supplies a real value
 * (never `undefined`) for all four, so the extra possibility in the type
 * never happens in practice; `useQuestionForm`'s `onSubmit` below still
 * runs the value through `questionWriteRequestSchema.parse` to hand
 * `createQuestion`/`updateQuestion` the properly-typed output shape.
 */
type QuestionFormValues = z.input<typeof questionWriteRequestSchema>;

/**
 * Fixed at four, never grown or shrunk — §10.2: "a configurable count buys
 * nothing and costs variability in the answer grid". A fresh array of
 * fresh item objects every call, never one shared literal two form
 * instances (or two calls from the same instance) could both mutate.
 */
export function blankChoices(): ChoiceWrite[] {
  return [
    { text: "", is_correct: true },
    { text: "", is_correct: false },
    { text: "", is_correct: false },
    { text: "", is_correct: false },
  ];
}

/**
 * The backend 422s on two correct choices
 * (`QuestionWriteRequest._shape` in
 * `backend/src/triviador/api/schemas/admin/questions.py`) — this is what
 * makes that state unreachable from the form instead of a save-time
 * surprise: exactly one `is_correct: true` survives every call, because
 * every other choice is explicitly set to `false` in the same pass.
 */
export function markCorrect(choices: ChoiceWrite[], index: number): ChoiceWrite[] {
  return choices.map((choice, i) => ({ ...choice, is_correct: i === index }));
}

function initialValues(
  question: QuestionDetail | undefined,
  firstCategoryId: string,
): QuestionFormValues {
  if (question === undefined) {
    return {
      kind: "multiple_choice",
      prompt: "",
      category_id: firstCategoryId,
      difficulty: "easy",
      media_asset_id: null,
      choices: blankChoices(),
      numeric_answer: null,
      unit: null,
    };
  }
  return {
    kind: question.kind,
    prompt: question.prompt,
    category_id: question.category_id,
    difficulty: question.difficulty,
    media_asset_id: question.media_asset_id,
    choices:
      question.kind === "multiple_choice"
        ? (question.choices ?? blankChoices()).map((choice) => ({
            text: choice.text,
            is_correct: choice.is_correct,
          }))
        : null,
    numeric_answer: question.numeric_answer,
    unit: question.unit,
  };
}

export type UseQuestionFormArgs =
  | {
      mode: "create";
      categories: CategoryView[];
      onSaved: (question: QuestionDetail, duplicateOf: string[]) => void;
    }
  | {
      mode: "edit";
      question: QuestionDetail;
      categories: CategoryView[];
      onSaved: (question: QuestionDetail, duplicateOf: string[]) => void;
    };

/**
 * One hook, one `useForm`, wired to the generated `questionWriteRequestSchema`
 * as its `onSubmit` validator — same house pattern as `useSignIn`/`useRedeem`
 * (Plan 6): the client's field-level rules (prompt 1–1000 chars, choice text
 * 1–200, unit ≤16) are the server's rules, not a second copy of them.
 *
 * That schema does *not* encode "exactly four choices" or "exactly one
 * correct" — those two live only in the backend's `QuestionWriteRequest`
 * `@model_validator(mode="after")` (a cross-field Pydantic validator, which
 * `codegen.mjs`'s JSON-schema pipeline has no way to project into a Zod
 * shape; confirmed by reading `generated/admin.ts`'s
 * `questionWriteRequestSchema`/`choiceWriteSchema` — no `.length(4)`, no
 * refinement, nothing). So this form makes both states unreachable by
 * construction instead of relying on a validator that cannot see them:
 * `blankChoices()` always produces exactly four, and `markCorrect` always
 * leaves exactly one `is_correct: true`. The same backend validator also
 * rejects a `numeric_answer`/`unit` on a multiple-choice question and
 * `choices` on a numeric one — `question-form.tsx`'s kind switch nulls the
 * fields that no longer apply for the same reason.
 */
export function useQuestionForm(args: UseQuestionFormArgs) {
  const [duplicateOf, setDuplicateOf] = useState<string[]>([]);
  const queryClient = useQueryClient();

  const mutation = useMutation({
    mutationFn: (body: QuestionWriteRequest) =>
      args.mode === "create" ? createQuestion(body) : updateQuestion(args.question.id, body),
    // House pattern (`role-control.tsx`, `preset-form.tsx`,
    // `use-create-category.ts`, ...) — questions were the sole exception
    // before this fix, and the exception was live: a saved edit never
    // reached any already-mounted questions list, `staleTime: Infinity`
    // meant that never self-corrected, and nothing on screen indicated
    // the row shown was stale. `questionsRoot()` is a prefix of every
    // filtered/paged list key, so one call invalidates all of them
    // regardless of which filter or page happens to be mounted.
    // `setQueryData` on the detail key mirrors what activate/deactivate
    // already did below in `question-form.tsx` — the freshly-saved
    // question is already in hand, no reason to wait for a refetch to
    // show it.
    onSuccess: (saved) => {
      queryClient.invalidateQueries({ queryKey: adminKeys.questionsRoot() });
      queryClient.setQueryData(adminKeys.question(saved.question.id), saved.question);
    },
  });

  const question = args.mode === "edit" ? args.question : undefined;

  const form = useForm({
    defaultValues: initialValues(question, args.categories[0]?.id ?? ""),
    validators: { onSubmit: questionWriteRequestSchema },
    onSubmit: async ({ value }) => {
      setDuplicateOf([]);
      // `.parse`, not a cast: `value`'s static type carries the schema's
      // input-side `| undefined` (see `QuestionFormValues` above) even
      // though this form never actually leaves one unset — `parse` both
      // narrows to the real `QuestionWriteRequest` output type
      // `createQuestion`/`updateQuestion` expect and applies the schema's
      // defaults for real, rather than assuming they already hold.
      const body = questionWriteRequestSchema.parse(value);
      const saved = await mutation.mutateAsync(body).catch(() => undefined);
      if (saved === undefined) return;
      setDuplicateOf(saved.duplicate_of);
      args.onSaved(saved.question, saved.duplicate_of);
    },
  });

  return {
    form,
    isSaving: mutation.isPending,
    duplicateOf,
    saveError: mutation.error instanceof ApiFetchError ? mutation.error : null,
  };
}
