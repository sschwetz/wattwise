import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { apiFetch } from "../api/client";

export interface JobTiming {
  interval_minutes: number | null;
  hour: number | null;
  minute: number | null;
}

/** The body of a save: an interval job sends interval_minutes, a daily job sends hour and minute. */
export type JobTimingInput =
  | { kind: "interval"; interval_minutes: number }
  | { kind: "daily"; hour: number; minute: number };

export interface AdminJob {
  id: string;
  label: string;
  kind: string;
  timing: JobTiming;
  default: JobTiming;
  is_default: boolean;
  min_minutes: number | null;
  next_run: string | null;
  last_run: string | null;
}

export interface AdminJobList {
  timezone: string;
  scheduler_running: boolean;
  jobs: AdminJob[];
}

const JOBS_KEY = ["admin", "jobs"] as const;
const BASE = "/api/v1/admin/jobs";

export function useAdminJobs() {
  return useQuery({
    queryKey: JOBS_KEY,
    queryFn: () => apiFetch<AdminJobList>(BASE),
  });
}

function useJobMutation<V>(request: (variables: V) => Promise<AdminJob>) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: request,
    onSuccess: () => queryClient.invalidateQueries({ queryKey: JOBS_KEY }),
  });
}

export function useSaveJob(jobId: string) {
  return useJobMutation((timing: JobTimingInput) =>
    apiFetch<AdminJob>(`${BASE}/${jobId}`, { method: "PUT", body: JSON.stringify(timing) }),
  );
}

export function useResetJob(jobId: string) {
  return useJobMutation(() => apiFetch<AdminJob>(`${BASE}/${jobId}`, { method: "DELETE" }));
}
