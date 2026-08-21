import type { CategoryView } from "@/shared/api/generated/admin";
import { adminErrorMessage } from "@/shared/lib/admin-errors";
import { Banner, Button, Input } from "@/shared/ui";
import { useCreateCategory } from "../model/use-create-category";

export interface CreateCategoryInlineProps {
  onCreated: (category: CategoryView) => void;
  onCancel: () => void;
}

const FIELD_LABEL_CLASS = "text-[10px] font-semibold uppercase tracking-[0.14em] text-ink-dim";

/**
 * §9.7 lists the admin screens exactly — `/admin/questions · /:id ·
 * /import`, `/admin/invites`, `/admin/presets` — and there is no category
 * screen among them; §10.3's importer (`_ensure_categories`) is the
 * spec's own intended cold-start path for populating categories. A
 * standalone category management screen would add scope Spec 1
 * deliberately omits, so this is deliberately NOT one.
 *
 * But §10.2 mandates this editor's category field, and on a fresh install
 * (no import run yet) that field has nothing to pick — a spec-mandated
 * control that cannot be used. This closes that hole without adding the
 * unlisted screen: create a category without leaving the question editor,
 * rendered inline by `question-form.tsx`'s own Category field, one level
 * of scope narrower than a screen.
 */
export function CreateCategoryInline({ onCreated, onCancel }: CreateCategoryInlineProps) {
  const { form, isSaving, saveError } = useCreateCategory({ onCreated });

  return (
    // A `<div>`, not a nested `<form>`: this renders inside
    // `question-form.tsx`'s own `<form>`, and HTML forbids a `<form>`
    // descendant of a `<form>` — React warns on it, and the browser's
    // `submit` event bubbling from the inner element to the outer one
    // triggers the OUTER (question) form's submit handler too, firing it
    // prematurely with whatever the question form happens to hold at
    // that moment. "Create category" below calls `form.handleSubmit()`
    // directly from its own `onClick` instead of relying on native form
    // submission, which sidesteps both problems.
    <div className="flex flex-col gap-3 border-2 border-line bg-raised p-4">
      {saveError !== null && (
        <Banner tone="bad" {...(saveError.code !== null ? { code: saveError.code } : {})}>
          {adminErrorMessage(saveError.code ?? "validation_failed", saveError.message)}
        </Banner>
      )}

      <form.Field name="name">
        {(field) => (
          <div className="flex flex-col gap-2">
            <label htmlFor="new-category-name" className={FIELD_LABEL_CLASS}>
              Category name
            </label>
            <Input
              id="new-category-name"
              value={field.state.value}
              onChange={(event) => field.handleChange(event.target.value)}
            />
            {field.state.meta.errors.length > 0 && (
              <p className="text-[11px] font-medium text-bad">
                {field.state.meta.errors[0]?.message}
              </p>
            )}
          </div>
        )}
      </form.Field>

      <form.Field name="slug">
        {(field) => (
          <div className="flex flex-col gap-2">
            <label htmlFor="new-category-slug" className={FIELD_LABEL_CLASS}>
              Slug
            </label>
            <Input
              id="new-category-slug"
              value={field.state.value}
              onChange={(event) => field.handleChange(event.target.value)}
            />
            {/* The pattern itself (lowercase, dash-separated) is not
             *  restated here — a mismatch fails `createCategoryRequestSchema`
             *  (generated straight from `CATEGORY_SLUG_PATTERN`), and this
             *  is where that failure's own message renders. */}
            {field.state.meta.errors.length > 0 && (
              <p className="text-[11px] font-medium text-bad">
                {field.state.meta.errors[0]?.message}
              </p>
            )}
          </div>
        )}
      </form.Field>

      <div className="flex items-center gap-3">
        <Button
          type="button"
          variant="ghost"
          disabled={isSaving}
          onClick={() => void form.handleSubmit()}
        >
          {isSaving ? "Creating…" : "Create category"}
        </Button>
        <Button type="button" variant="ghost" onClick={onCancel} disabled={isSaving}>
          Cancel
        </Button>
      </div>
    </div>
  );
}
