import { useQuery } from "@tanstack/react-query";
import { apiFetch } from "../api/client";

export interface ServiceStatus {
  service: string;
  commit: string | null;
  last_reported_at: string | null;
  reporting: boolean;
}

export function useServices(enabled: boolean) {
  return useQuery({
    queryKey: ["services"],
    queryFn: () => apiFetch<ServiceStatus[]>("/api/v1/services"),
    enabled,
    refetchInterval: 30_000,
  });
}
