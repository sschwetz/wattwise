import { useQuery } from "@tanstack/react-query";
import { apiFetch } from "../api/client";

export interface ServiceStatus {
  service: string;
  commit: string | null;
  last_reported_at: string | null;
  reporting: boolean;
}

export function useServices() {
  return useQuery({
    queryKey: ["services"],
    queryFn: () => apiFetch<ServiceStatus[]>("/api/v1/services"),
    refetchInterval: 30_000,
  });
}
