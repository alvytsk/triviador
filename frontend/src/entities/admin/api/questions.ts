import { keepPreviousData, queryOptions } from "@tanstack/react-query";
import { apiFetch, apiSend } from "@/shared/api";
import {
  type QuestionDetail,
  type QuestionSaved,
  type QuestionWriteRequest,
  questionDetailSchema,
  questionPageViewSchema,
  questionSavedSchema,
} from "@/shared/api/generated/admin";
import { adminKeys, type Page, type QuestionFilters } from "../model/keys";

export interface AdminQuestionSearch {
  filters: QuestionFilters;
  page: Page;
}

/** `undefined`/`""` filters are omitted rather than sent empty: the
 *  backend's own query params are all optional, and a present-but-empty
 *  `category_id=` would ask it to filter on the empty string instead of not
 *  filtering at all. */
function toQuery(search: AdminQuestionSearch): string {
  const params = new URLSearchParams();
  const { filters, page } = search;
  if (filters.kind !== undefined) params.set("kind", filters.kind);
  if (filters.categoryId !== undefined) params.set("category_id", filters.categoryId);
  if (filters.difficulty !== undefined) params.set("difficulty", filters.difficulty);
  if (filters.isActive !== undefined) params.set("is_active", String(filters.isActive));
  if (filters.hasMedia !== undefined) params.set("has_media", String(filters.hasMedia));
  if (filters.q !== undefined && filters.q !== "") params.set("q", filters.q);
  params.set("limit", String(page.limit));
  params.set("offset", String(page.offset));
  return params.toString();
}

/** The list is a table an admin scans and re-filters; showing the last
 *  page while the next loads is better than a spinner on every keystroke. */
export function adminQuestionsQueryOptions(search: AdminQuestionSearch) {
  return queryOptions({
    queryKey: adminKeys.questions(search.filters, search.page),
    queryFn: () => apiFetch(`/api/admin/questions?${toQuery(search)}`, questionPageViewSchema),
    placeholderData: keepPreviousData,
  });
}

export function adminQuestionQueryOptions(id: string) {
  return queryOptions({
    queryKey: adminKeys.question(id),
    queryFn: () => apiFetch(`/api/admin/questions/${id}`, questionDetailSchema),
  });
}

export function createQuestion(body: QuestionWriteRequest): Promise<QuestionSaved> {
  return apiSend("/api/admin/questions", questionSavedSchema, body);
}

export function updateQuestion(id: string, body: QuestionWriteRequest): Promise<QuestionSaved> {
  return apiSend(`/api/admin/questions/${id}`, questionSavedSchema, body, { method: "PATCH" });
}

export function deactivateQuestion(id: string): Promise<QuestionDetail> {
  return apiSend(`/api/admin/questions/${id}/deactivate`, questionDetailSchema, undefined);
}

export function activateQuestion(id: string): Promise<QuestionDetail> {
  return apiSend(`/api/admin/questions/${id}/activate`, questionDetailSchema, undefined);
}
