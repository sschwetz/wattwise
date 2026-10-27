import { useMemo } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { apiFetch } from "../api/client";

export interface Site {
  id: number;
  owner_user_id: number;
  name: string;
  timezone: string;
  enabled: boolean;
}

interface UpdateSiteInput {
  name?: string;
  timezone?: string;
  enabled?: boolean;
}

interface CreateSiteInput {
  name: string;
  timezone: string;
}

export function useSites() {
  return useQuery({
    queryKey: ["sites"],
    queryFn: () => apiFetch<Site[]>("/api/v1/sites"),
    // Many billing components read the site's time zone; share one fetch.
    staleTime: 60_000,
  });
}

export function useCreateSite() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (input: CreateSiteInput) =>
      apiFetch<Site>("/api/v1/sites", { method: "POST", body: JSON.stringify(input) }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["sites"] });
    },
  });
}

/** The sites query with `data` narrowed to enabled sites. */
export function useEnabledSites() {
  const query = useSites();
  const data = useMemo(() => query.data?.filter((site) => site.enabled), [query.data]);
  return { ...query, data };
}

export function useUpdateSite(siteId: number) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (input: UpdateSiteInput) =>
      apiFetch<Site>(`/api/v1/sites/${siteId}`, { method: "PATCH", body: JSON.stringify(input) }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["sites"] });
    },
  });
}
