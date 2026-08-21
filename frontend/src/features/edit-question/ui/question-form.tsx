import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";
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
import { CreateCategoryInline } from "./create-category-inline";
import { MediaField } from "./media-field";

export type QuestionFormProps =
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
  const [isCreatingCategory, setIsCreatingCategory] = useState(false);
  // The category the inline picker just created, kept independently of
  // the `categories` prop's own async refetch (`useCreateCategory`
  // invalidates the categories query, but that round trip does not
  // resolve within this same render). Traced this down to a real bug,
  // not a guess: Radix Select keeps a hidden native `<select>` in sync
  // via its own `useEffect` (`SelectBubbleInput` in
  // `@radix-ui/react-select`), which programmatically sets that
  // element's `.value` to the new selection and dispatches a `change`
  // event. Setting a native `<select>`'s `.value` to something with no
  // matching `<option>` is a silent no-op per the DOM spec — the element
  // just keeps its old value — so the dispatched event reports the OLD
  // value back through `onValueChange`, which is indistinguishable from
  // the admin picking "nothing". Confirmed by instrumenting
  // `@tanstack/form-core`'s own `setState`: the value flipped from the
  // created id straight back to `""`, called from Radix's
  // `BubbleSelect` sync, not from anything in this file. `categoryOptions`
  // below adds a matching `SelectItem` in the render BEFORE the value
  // changes (see the `useEffect` after this block, which defers the
  // actual field write by one commit for exactly that reason) so the
  // native `<option>` genuinely exists by the time Radix's effect looks
  // for it.
  const [justCreatedCategory, setJustCreatedCategory] = useState<CategoryView | null>(null);
  const queryClient = useQueryClient();

  // Deferred by design, not an oversight — see the comment above.
  // Setting `justCreatedCategory` and the field value in the SAME
  // commit would render the new `SelectItem` and change the selected
  // value together, which is exactly the ordering that trips Radix's
  // native-select sync. Splitting it into two renders — this state
  // update lands the `SelectItem` first, then this effect writes the
  // field value on the NEXT commit — gives the native `<option>` a
  // chance to exist before Radix looks for it.
  useEffect(() => {
    if (justCreatedCategory !== null) {
      form.setFieldValue("category_id", justCreatedCategory.id);
    }
  }, [justCreatedCategory, form]);

  const activate = useMutation({
    mutationFn: () => activateQuestion(question?.id ?? ""),
    onSuccess: (updated) => {
      setIsActive(updated.is_active);
      if (question !== undefined) {
        queryClient.setQueryData(adminKeys.question(question.id), updated);
        // Was missing before this fix: setQueryData above only ever
        // reached this SCREEN's own detail cache entry — a list the
        // admin had already visited (e.g. filtered to "Active only")
        // kept showing this row under its pre-toggle state for the rest
        // of the session, `staleTime: Infinity` (`app/query-client.ts`)
        // meaning that state never self-corrected.
        queryClient.invalidateQueries({ queryKey: adminKeys.questionsRoot() });
      }
    },
  });
  const deactivate = useMutation({
    mutationFn: () => deactivateQuestion(question?.id ?? ""),
    onSuccess: (updated) => {
      setIsActive(updated.is_active);
      if (question !== undefined) {
        queryClient.setQueryData(adminKeys.question(question.id), updated);
        queryClient.invalidateQueries({ queryKey: adminKeys.questionsRoot() });
      }
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
          {(field) => {
            // `categories` plus the just-created one, deduped — see the
            // `justCreatedCategory` state comment above for why this
            // can't just wait for `categories` itself to catch up.
            const categoryOptions =
              justCreatedCategory !== null &&
              !categories.some((category) => category.id === justCreatedCategory.id)
                ? [...categories, justCreatedCategory]
                : categories;
            return (
              <div className="flex flex-1 flex-col gap-2">
                <div className="flex items-center justify-between gap-2">
                  <span className={FIELD_LABEL_CLASS}>Category</span>
                  {/* The only click path anywhere in the app that
                   *  creates a category (§10.2 mandates this field;
                   *  §9.7 lists no category screen — see
                   *  `create-category-inline.tsx`'s comment for the
                   *  full scope ruling). Hidden, not disabled, while
                   *  the inline form is already open. */}
                  {!isCreatingCategory && (
                    <button
                      type="button"
                      onClick={() => setIsCreatingCategory(true)}
                      className="text-[11px] font-semibold uppercase tracking-[0.14em] text-ink-dim hover:text-ink"
                    >
                      New category
                    </button>
                  )}
                </div>
                <Select
                  value={field.state.value}
                  onValueChange={(value) => field.handleChange(value)}
                >
                  <SelectTrigger aria-label="Category">
                    <SelectValue placeholder="Choose a category" />
                  </SelectTrigger>
                  <SelectContent>
                    {categoryOptions.map((category) => (
                      <SelectItem key={category.id} value={category.id}>
                        {category.name}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
                {isCreatingCategory && (
                  <CreateCategoryInline
                    onCreated={(created) => {
                      // Select it, same as picking it from the dropdown
                      // — the whole point is not making the admin
                      // re-open the picker to find what they just
                      // typed. Only `justCreatedCategory` here, NOT
                      // `form.setFieldValue` too — the `useEffect` above
                      // writes the field value one commit later, on
                      // purpose (see its comment and the state comment
                      // above for why that ordering matters).
                      setJustCreatedCategory(created);
                      setIsCreatingCategory(false);
                    }}
                    onCancel={() => setIsCreatingCategory(false)}
                  />
                )}
              </div>
            );
          }}
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
                // `state.fieldMeta`, not this field's own `field.state.meta`:
                // `questionWriteRequestSchema`'s `onSubmit` validator reports
                // an over-long choice against the PER-ITEM path
                // `choices[<i>].text` (see `standardSchemaValidator.js`'s
                // `prefixSchemaToErrors`), not against `choices` itself — so
                // reading only this field's own meta would never see it.
                // Subscribing to the whole map is what makes those per-item
                // errors visible without giving each choice input its own
                // `form.Field` (which would also work, but re-plumbing
                // `ChoiceEditor`'s onChange/onMarkCorrect callbacks through
                // four extra `form.Field`s is a bigger change than fixing
                // one restated bound calls for).
                <form.Subscribe selector={(state) => state.fieldMeta}>
                  {(fieldMeta) => (
                    <ChoiceEditor
                      choices={field.state.value ?? blankChoices()}
                      errors={(field.state.value ?? blankChoices()).map((_, index) => {
                        const meta = fieldMeta[`choices[${index}].text`];
                        return meta?.errors[0]?.message ?? null;
                      })}
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
                </form.Subscribe>
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
