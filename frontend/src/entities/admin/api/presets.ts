import { queryOptions } from "@tanstack/react-query";
import { z } from "zod";
import { apiFetch, apiSend, type PresetSummary, presetSummarySchema } from "@/shared/api";
import {
  type PresetDetail,
  type PresetWriteRequest,
  presetCoverageSchema,
  presetDetailSchema,
} from "@/shared/api/generated/admin";
import { adminKeys } from "../model/keys";

/** Retired presets included — the admin screen shows them (Spec 1B §6.1's
 *  soft delete), where the public listing must not. */
export function adminPresetsQueryOptions() {
  return queryOptions({
    queryKey: adminKeys.presets(),
    queryFn: () => apiFetch("/api/admin/presets", z.array(presetDetailSchema)),
  });
}

export function adminPresetQueryOptions(id: string) {
  return queryOptions({
    queryKey: adminKeys.preset(id),
    queryFn: () => apiFetch(`/api/admin/presets/${id}`, presetDetailSchema),
  });
}

export function adminPresetCoverageQueryOptions(id: string) {
  return queryOptions({
    queryKey: adminKeys.coverage(id),
    queryFn: () => apiFetch(`/api/admin/presets/${id}/coverage`, presetCoverageSchema),
  });
}

export function createPreset(body: PresetWriteRequest): Promise<PresetDetail> {
  return apiSend("/api/admin/presets", presetDetailSchema, body);
}

export function updatePreset(id: string, body: PresetWriteRequest): Promise<PresetDetail> {
  return apiSend(`/api/admin/presets/${id}`, presetDetailSchema, body, { method: "PATCH" });
}

/** §6.1's soft delete: `DELETE` retires the preset rather than removing
 *  its row, and answers 204 with no body. */
export function deactivatePreset(id: string): Promise<void> {
  return apiSend(`/api/admin/presets/${id}`, z.void(), undefined, { method: "DELETE" });
}

/** `GET /api/presets` — the one route on this slice's surface that is not
 *  under `/api/admin` (Plan 7A Decision 1): any signed-in user, not just an
 *  admin, may read it, and it answers only active presets. */
export function publicPresetsQueryOptions() {
  return queryOptions({
    queryKey: adminKeys.publicPresets(),
    queryFn: (): Promise<PresetSummary[]> => apiFetch("/api/presets", z.array(presetSummarySchema)),
  });
}
