import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { apiFetch } from "../api/client";

export interface Site {
  id: number;
  owner_user_id: number;
  name: string;
  timezone: string;
}

interface CreateSiteInput {
  name: string;
  timezone: string;
}

export function useSites() {
  return useQuery({
    queryKey: ["sites"],
    queryFn: () => apiFetch<Site[]>("/api/v1/sites"),
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
