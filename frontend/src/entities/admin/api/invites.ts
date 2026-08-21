import { queryOptions } from "@tanstack/react-query";
import { z } from "zod";
import { apiFetch, apiSend } from "@/shared/api";
import {
  type InviteView,
  type IssuedInvite,
  type IssueInvitesRequest,
  inviteViewSchema,
  issuedInviteSchema,
} from "@/shared/api/generated/admin";
import { adminKeys } from "../model/keys";

export function adminInvitesQueryOptions() {
  return queryOptions({
    queryKey: adminKeys.invites(),
    queryFn: () => apiFetch("/api/admin/invites", z.array(inviteViewSchema)),
  });
}

export function issueInvites(body: IssueInvitesRequest): Promise<IssuedInvite[]> {
  return apiSend("/api/admin/invites", z.array(issuedInviteSchema), body);
}

export function revokeInvite(id: string): Promise<InviteView> {
  return apiSend(`/api/admin/invites/${id}/revoke`, inviteViewSchema, undefined);
}
