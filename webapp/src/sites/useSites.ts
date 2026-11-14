import { useMemo } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { apiFetch } from "../api/client";

export interface Site {
  id: number;
  owner_user_id: number;
  name: string;
  timezone: string;
  enabled: boolean;
  latitude: number | null;
  longitude: number | null;
}

interface UpdateSiteInput {
  name?: string;
  timezone?: string;
  enabled?: boolean;
  /** Sent together; null clears the location. */
  latitude?: number | null;
  longitude?: number | null;
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

/** Today's sunrise and sunset at the site. `estimated` is true while the time zone, not a point, supplies the position. */
export interface SiteSun {
  estimated: boolean;
  sunrise: string | null;
  sunset: string | null;
}

export function useSiteSun(site: Pick<Site, "id" | "latitude" | "longitude" | "timezone">) {
  return useQuery({
    // The point and zone are part of the key, so saving a new location fetches the new times.
    queryKey: ["site-sun", site.id, site.latitude, site.longitude, site.timezone],
    queryFn: () => apiFetch<SiteSun>(`/api/v1/sites/${site.id}/sun`),
    staleTime: 60 * 60_000,
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
