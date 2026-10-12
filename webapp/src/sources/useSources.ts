import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { apiFetch } from "../api/client";

export interface Source {
  id: number;
  site_id: number;
  kind: string;
  provider: string;
  name: string | null;
  enabled: boolean;
  config: Record<string, string | boolean>;
}

interface CreateSourceInput {
  kind: string;
  provider: string;
  name?: string;
}

interface UpdateSourceInput {
  sourceId: number;
  enabled?: boolean;
  config?: Record<string, string | boolean>;
  name?: string;
}

export function useSources(siteId: number) {
  return useQuery({
    queryKey: ["sources", siteId],
    queryFn: () => apiFetch<Source[]>(`/api/v1/sites/${siteId}/sources`),
  });
}

export function useCreateSource(siteId: number) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (input: CreateSourceInput) =>
      apiFetch<Source>(`/api/v1/sites/${siteId}/sources`, {
        method: "POST",
        body: JSON.stringify({ ...input, config: {} }),
      }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["sources", siteId] });
    },
  });
}

export function useUpdateSource(siteId: number) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ sourceId, enabled, config, name }: UpdateSourceInput) =>
      apiFetch<Source>(`/api/v1/sites/${siteId}/sources/${sourceId}`, {
        method: "PATCH",
        body: JSON.stringify({ enabled, config, name }),
      }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["sources", siteId] });
    },
  });
}

export function useDeleteSource(siteId: number) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (sourceId: number) =>
      apiFetch<void>(`/api/v1/sites/${siteId}/sources/${sourceId}`, { method: "DELETE" }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["sources", siteId] });
    },
  });
}
