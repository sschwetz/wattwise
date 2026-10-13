import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { apiFetch } from "../api/client";

export type RateChannel = "buy" | "sell" | "controlled_load";

export interface RateWindow {
  id: number;
  source_id: number;
  channel: RateChannel;
  label: string;
  effective_from: string;
  effective_to: string | null;
  months: number[];
  days_of_week: number[];
  start_time: string;
  end_time: string;
  // Serialized by core-api as a decimal string, not a JSON number.
  rate_c_per_kwh: string;
}

export interface RateWindowInput {
  channel: RateChannel;
  label: string;
  effective_from: string;
  effective_to: string | null;
  months: number[];
  days_of_week: number[];
  start_time: string;
  end_time: string;
  rate_c_per_kwh: number;
}

function basePath(siteId: number, sourceId: number): string {
  return `/api/v1/sites/${siteId}/sources/${sourceId}/rate-windows`;
}

function queryKey(siteId: number, sourceId: number) {
  return ["rate-windows", siteId, sourceId];
}

export function useRateWindows(siteId: number, sourceId: number) {
  return useQuery({
    queryKey: queryKey(siteId, sourceId),
    queryFn: () => apiFetch<RateWindow[]>(basePath(siteId, sourceId)),
  });
}

export function useCreateRateWindow(siteId: number, sourceId: number) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (input: RateWindowInput) =>
      apiFetch<RateWindow>(basePath(siteId, sourceId), { method: "POST", body: JSON.stringify(input) }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: queryKey(siteId, sourceId) }),
  });
}

export function useReplaceRateWindow(siteId: number, sourceId: number) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ windowId, input }: { windowId: number; input: RateWindowInput }) =>
      apiFetch<RateWindow>(`${basePath(siteId, sourceId)}/${windowId}`, {
        method: "PATCH",
        body: JSON.stringify(input),
      }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: queryKey(siteId, sourceId) }),
  });
}

export function useDeleteRateWindow(siteId: number, sourceId: number) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (windowId: number) =>
      apiFetch<void>(`${basePath(siteId, sourceId)}/${windowId}`, { method: "DELETE" }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: queryKey(siteId, sourceId) }),
  });
}
