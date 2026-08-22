import { queryOptions } from "@tanstack/react-query";
import { z } from "zod";
import { apiFetch, apiSend } from "@/shared/api";
import {
  type CategoryView,
  type CreateCategoryRequest,
  categoryViewSchema,
  type RenameCategoryRequest,
} from "@/shared/api/generated/admin";
import { adminKeys } from "../model/keys";

export function adminCategoriesQueryOptions() {
  return queryOptions({
    queryKey: adminKeys.categories(),
    queryFn: () => apiFetch("/api/admin/categories", z.array(categoryViewSchema)),
  });
}

export function createCategory(body: CreateCategoryRequest): Promise<CategoryView> {
  return apiSend("/api/admin/categories", categoryViewSchema, body);
}

export function renameCategory(id: string, body: RenameCategoryRequest): Promise<CategoryView> {
  return apiSend(`/api/admin/categories/${id}`, categoryViewSchema, body, {
    method: "PATCH",
  });
}
