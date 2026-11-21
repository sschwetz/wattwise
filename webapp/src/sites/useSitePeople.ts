import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { apiFetch } from "../api/client";
import type { Site } from "./useSites";

export interface SitePerson {
  user_id: number;
  email: string;
  display_name: string | null;
  role: Site["my_role"];
}

/** Everyone with access to the site. The API answers only the owner and managers, so pass `enabled` false for anyone else. */
export function useSitePeople(siteId: number, enabled = true) {
  return useQuery({
    queryKey: ["sitePeople", siteId],
    queryFn: () => apiFetch<SitePerson[]>(`/api/v1/sites/${siteId}/access`),
    enabled,
  });
}

/** Owner only. Also revokes the person's invitations to the site, so the invite list refreshes too. */
export function useRemoveSitePerson(siteId: number) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (userId: number) => apiFetch<void>(`/api/v1/sites/${siteId}/access/${userId}`, { method: "DELETE" }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["sitePeople", siteId] });
      queryClient.invalidateQueries({ queryKey: ["siteInvites", siteId] });
    },
  });
}
