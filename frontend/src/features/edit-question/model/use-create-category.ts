import { useForm } from "@tanstack/react-form";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { adminKeys, createCategory } from "@/entities/admin";
import { ApiFetchError } from "@/shared/api";
import {
  type CategoryView,
  type CreateCategoryRequest,
  createCategoryRequestSchema,
} from "@/shared/api/generated/admin";

export interface UseCreateCategoryArgs {
  onCreated: (category: CategoryView) => void;
}

/**
 * Backs the question editor's inline "New category" affordance — see
 * `create-category-inline.tsx`'s own comment for the scope ruling on why
 * this lives inside the existing category picker rather than as a
 * standalone screen (§9.7 lists no category screen, and §10.3's importer
 * is the spec's intended cold-start path).
 *
 * Same house pattern as `useQuestionForm`: the generated
 * `createCategoryRequestSchema` — not a hand-rolled copy of
 * `CATEGORY_SLUG_PATTERN` — is the only source of the field-level rules
 * (name 1–64 chars, slug 1–48 chars matching the backend's pattern),
 * wired as this form's own `validators.onSubmit`. `slug_taken` (409) is
 * a server-side refusal no client-side rule could catch anyway — it
 * surfaces through `saveError` below and `adminErrorMessage` renders its
 * fixed sentence (`shared/lib/admin-errors.ts`'s override list still
 * carries it, verified single-site at `api/http/admin/categories.py`).
 */
export function useCreateCategory({ onCreated }: UseCreateCategoryArgs) {
  const queryClient = useQueryClient();

  const mutation = useMutation({
    mutationFn: (body: CreateCategoryRequest) => createCategory(body),
    onSuccess: (created) => {
      // So the category picker — and any other screen reading this list —
      // sees the new row without a manual refetch.
      queryClient.invalidateQueries({ queryKey: adminKeys.categories() });
      onCreated(created);
    },
  });

  const form = useForm({
    defaultValues: { name: "", slug: "" } satisfies CreateCategoryRequest,
    validators: { onSubmit: createCategoryRequestSchema },
    onSubmit: async ({ value }) => {
      const body = createCategoryRequestSchema.parse(value);
      const created = await mutation.mutateAsync(body).catch(() => undefined);
      if (created === undefined) return;
      form.reset();
    },
  });

  return {
    form,
    isSaving: mutation.isPending,
    saveError: mutation.error instanceof ApiFetchError ? mutation.error : null,
  };
}
