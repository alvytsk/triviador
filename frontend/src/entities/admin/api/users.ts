import { queryOptions } from "@tanstack/react-query";
import { z } from "zod";
import { apiFetch, apiSend } from "@/shared/api";
import { type SetRoleRequest, type UserView, userViewSchema } from "@/shared/api/generated/admin";
import { adminKeys } from "../model/keys";

export function adminUsersQueryOptions() {
  return queryOptions({
    queryKey: adminKeys.users(),
    queryFn: () => apiFetch("/api/admin/users", z.array(userViewSchema)),
  });
}

export function deactivateUser(id: string): Promise<UserView> {
  return apiSend(`/api/admin/users/${id}/deactivate`, userViewSchema, undefined);
}

export function setUserRole(id: string, body: SetRoleRequest): Promise<UserView> {
  return apiSend(`/api/admin/users/${id}/role`, userViewSchema, body);
}
