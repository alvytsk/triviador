import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { activateQuestion, adminKeys, deactivateQuestion } from "@/entities/admin";
import type {
  CategoryView,
  Difficulty,
  QuestionDetail,
  QuestionKind,
} from "@/shared/api/generated/admin";
import { adminErrorMessage } from "@/shared/lib/admin-errors";
import {
  Banner,
  Button,
  Chip,
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/shared/ui";
import { blankChoices, markCorrect, useQuestionForm } from "../model/use-question-form";
import { ChoiceEditor } from "./choice-editor";
import { MediaField } from "./media-field";

export type QuestionFormProps =
  | { mode: "create"; categories: CategoryView[]; onSaved: (question: QuestionDetail) => void }
  | {
      mode: "edit";
      question: QuestionDetail;
      categories: CategoryView[];
      onSaved: (question: QuestionDetail) => void;
    };

const DIFFICULTIES: Difficulty[] = ["easy", "medium", "hard"];
const DIFFICULTY_LABEL: Record<Difficulty, string> = {
  easy: "Easy",
  medium: "Medium",
  hard: "Hard",
};

const FIELD_LABEL_CLASS = "text-[10px] font-semibold uppercase tracking-[0.14em] text-ink-dim";
const TEXT_INPUT_CLASS =
  "bg-raised border-2 border-line px-4 py-3 text-[15px] font-medium text-ink outline-none focus:border-gold";

/**
 * §10.2's one form for both question kinds: common fields (prompt,
 * category, difficulty, media, active state) plus whichever kind-specific
 * section `kind` currently selects. See `use-question-form.ts` for why the
 * two hard invariants (exactly four choices, exactly one correct) are
 * enforced by construction here rather than by a validator — the
 * generated schema has no way to express either.
 */
export function QuestionForm(props: QuestionFormProps) {
  const question = props.mode === "edit" ? props.question : undefined;
  const { categories, onSaved } = props;

  const { form, isSaving, duplicateOf, saveError } = useQuestionForm(
    props.mode === "edit"
      ? { mode: "edit", question: props.question, categories, onSaved }
      : { mode: "create", categories, onSaved },
  );

  const [isActive, setIsActive] = useState(question?.is_active ?? true);
  const queryClient = useQueryClient();

  const activate = useMutation({
    mutationFn: () => activateQuestion(question?.id ?? ""),
    onSuccess: (updated) => {
      setIsActive(updated.is_active);
      if (question !== undefined)
        queryClient.setQueryData(adminKeys.question(question.id), updated);
    },
  });
  const deactivate = useMutation({
    mutationFn: () => deactivateQuestion(question?.id ?? ""),
    onSuccess: (updated) => {
      setIsActive(updated.is_active);
      if (question !== undefined)
        queryClient.setQueryData(adminKeys.question(question.id), updated);
    },
  });

  /**
   * The kind-conditional half of the shape rule the backend's
   * `QuestionWriteRequest._shape` enforces (see `use-question-form.ts`):
   * switching kind nulls out whichever fields the new kind carries none
   * of, so the payload this form builds is never the "numeric question
   * with leftover choices" shape a 422 would otherwise catch at save time.
   * `prompt`/`category_id`/`difficulty`/`media_asset_id` are untouched —
   * losing those on a kind change is the exact failure the brief calls
   * out ("a form that resets on kind change loses an admin's typing at
   * the exact moment they realise they picked the wrong kind").
   */
  function handleKindChange(next: QuestionKind) {
    form.setFieldValue("kind", next);
    if (next === "multiple_choice") {
      form.setFieldValue("choices", blankChoices());
      form.setFieldValue("numeric_answer", null);
      form.setFieldValue("unit", null);
    } else {
      form.setFieldValue("choices", null);
    }
  }

  return (
    <form
      className="flex max-w-2xl flex-col gap-6"
      onSubmit={(event) => {
        event.preventDefault();
        void form.handleSubmit();
      }}
    >
      {question !== undefined && (
        <div className="flex items-center justify-between border-2 border-line bg-panel px-4 py-3">
          <Chip className={isActive ? "" : "bg-track text-ink-faint"}>
            {isActive ? "Active" : "Inactive"}
          </Chip>
          <Button
            type="button"
            variant="ghost"
            disabled={activate.isPending || deactivate.isPending}
            onClick={() => (isActive ? deactivate.mutate() : activate.mutate())}
          >
            {isActive ? "Deactivate" : "Activate"}
          </Button>
        </div>
      )}

      {saveError !== null && (
        <Banner tone="bad" {...(saveError.code !== null ? { code: saveError.code } : {})}>
          {adminErrorMessage(saveError.code ?? "validation_failed", saveError.message)}
        </Banner>
      )}

      {/* A warning, never a blocking error (§10.2): the save already
       *  succeeded by the time this can show. */}
      {duplicateOf.length > 0 && (
        <Banner tone="warn">
          Saved — but this prompt closely matches {duplicateOf.length} existing question
          {duplicateOf.length === 1 ? "" : "s"} already in the bank.
        </Banner>
      )}

      <form.Field name="prompt">
        {(field) => (
          <div className="flex flex-col gap-2">
            <label htmlFor="question-prompt" className={FIELD_LABEL_CLASS}>
              Prompt
            </label>
            <textarea
              id="question-prompt"
              value={field.state.value}
              onChange={(event) => field.handleChange(event.target.value)}
              rows={3}
              className={TEXT_INPUT_CLASS}
            />
            {field.state.meta.errors.length > 0 && (
              <p className="text-[11px] font-medium text-bad">
                {field.state.meta.errors[0]?.message}
              </p>
            )}
          </div>
        )}
      </form.Field>

      <div className="flex gap-4">
        <form.Field name="category_id">
          {(field) => (
            <div className="flex flex-1 flex-col gap-2">
              <span className={FIELD_LABEL_CLASS}>Category</span>
              <Select
                value={field.state.value}
                onValueChange={(value) => field.handleChange(value)}
              >
                <SelectTrigger aria-label="Category">
                  <SelectValue placeholder="Choose a category" />
                </SelectTrigger>
                <SelectContent>
                  {categories.map((category) => (
                    <SelectItem key={category.id} value={category.id}>
                      {category.name}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
          )}
        </form.Field>

        <form.Field name="difficulty">
          {(field) => (
            <div className="flex flex-1 flex-col gap-2">
              <span className={FIELD_LABEL_CLASS}>Difficulty</span>
              <Select
                value={field.state.value}
                onValueChange={(value) => field.handleChange(value as Difficulty)}
              >
                <SelectTrigger aria-label="Difficulty">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {DIFFICULTIES.map((difficulty) => (
                    <SelectItem key={difficulty} value={difficulty}>
                      {DIFFICULTY_LABEL[difficulty]}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
          )}
        </form.Field>

        <form.Field name="kind">
          {(field) => (
            <div className="flex flex-1 flex-col gap-2">
              <span className={FIELD_LABEL_CLASS}>Kind</span>
              <Select
                value={field.state.value}
                onValueChange={(value) => handleKindChange(value as QuestionKind)}
              >
                <SelectTrigger aria-label="Kind">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="multiple_choice">Multiple choice</SelectItem>
                  <SelectItem value="numeric">Numeric</SelectItem>
                </SelectContent>
              </Select>
            </div>
          )}
        </form.Field>
      </div>

      <form.Field name="media_asset_id">
        {(field) => (
          <MediaField mediaAssetId={field.state.value ?? null} onChange={field.handleChange} />
        )}
      </form.Field>

      <form.Subscribe selector={(state) => state.values.kind}>
        {(kind) =>
          kind === "multiple_choice" ? (
            <form.Field name="choices">
              {(field) => (
                <ChoiceEditor
                  choices={field.state.value ?? blankChoices()}
                  onTextChange={(index, text) =>
                    field.handleChange(
                      (field.state.value ?? blankChoices()).map((choice, i) =>
                        i === index ? { ...choice, text } : choice,
                      ),
                    )
                  }
                  onMarkCorrect={(index) =>
                    field.handleChange(markCorrect(field.state.value ?? blankChoices(), index))
                  }
                />
              )}
            </form.Field>
          ) : (
            <div className="flex gap-4">
              <form.Field name="numeric_answer">
                {(field) => (
                  <div className="flex flex-1 flex-col gap-2">
                    <label htmlFor="question-numeric-answer" className={FIELD_LABEL_CLASS}>
                      Correct value
                    </label>
                    <input
                      id="question-numeric-answer"
                      value={field.state.value ?? ""}
                      onChange={(event) =>
                        field.handleChange(event.target.value === "" ? null : event.target.value)
                      }
                      className={TEXT_INPUT_CLASS}
                    />
                  </div>
                )}
              </form.Field>
              <form.Field name="unit">
                {(field) => (
                  <div className="flex flex-1 flex-col gap-2">
                    <label htmlFor="question-unit" className={FIELD_LABEL_CLASS}>
                      Unit (optional)
                    </label>
                    <input
                      id="question-unit"
                      value={field.state.value ?? ""}
                      onChange={(event) =>
                        field.handleChange(event.target.value === "" ? null : event.target.value)
                      }
                      className={TEXT_INPUT_CLASS}
                    />
                  </div>
                )}
              </form.Field>
            </div>
          )
        }
      </form.Subscribe>

      <Button type="submit" disabled={isSaving}>
        {isSaving ? "Saving…" : props.mode === "create" ? "Create question" : "Save changes"}
      </Button>
    </form>
  );
}
