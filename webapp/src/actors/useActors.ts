import { useMutation, useQuery, useQueryClient, type QueryClient } from "@tanstack/react-query";
import { apiFetch } from "../api/client";
import { LIVE_POLL_MS } from "../dashboard/useLiveReadings";
import { hasActiveRun } from "./runFormat";
import type {
  ActorFailure,
  ActorInstance,
  ActorRun,
  ActorRunListItem,
  ActorSchedule,
  InstanceCreateBody,
  InstancePatchBody,
  ScheduleBody,
  SchedulePatchBody,
} from "./types";

export const FAILURES_POLL_MS = 15_000;
/** Poll rate for run lists while a run is queued or running. */
export const RUNS_ACTIVE_POLL_MS = 5_000;
/** Poll rate while a sign-in panel waits for the user to approve the code. */
export const INSTANCES_SIGN_IN_POLL_MS = 10_000;

export const actorKeys = {
  all: (siteId: number) => ["actors", siteId] as const,
  instances: (siteId: number) => ["actors", siteId, "instances"] as const,
  schedules: (siteId: number) => ["actors", siteId, "schedules"] as const,
  runs: (siteId: number) => ["actors", siteId, "runs"] as const,
  failures: (siteId: number) => ["actors", siteId, "failures"] as const,
};

const base = (siteId: number) => `/api/v1/sites/${siteId}/actors`;
const validSite = (siteId: number) => Number.isFinite(siteId);

function awaitingApproval(instances: ActorInstance[] | undefined): boolean {
  return (instances ?? []).some((i) => i.auth_state !== null && "user_code" in i.auth_state);
}

export function useActorInstances(siteId: number) {
  return useQuery({
    queryKey: actorKeys.instances(siteId),
    queryFn: () => apiFetch<ActorInstance[]>(`${base(siteId)}/instances`),
    enabled: validSite(siteId),
    refetchInterval: (query) => (awaitingApproval(query.state.data) ? INSTANCES_SIGN_IN_POLL_MS : LIVE_POLL_MS),
  });
}

export function useSchedules(siteId: number) {
  return useQuery({
    queryKey: actorKeys.schedules(siteId),
    queryFn: () => apiFetch<ActorSchedule[]>(`${base(siteId)}/schedules`),
    enabled: validSite(siteId),
  });
}

export function useRuns(siteId: number, limit: number) {
  return useQuery({
    queryKey: [...actorKeys.runs(siteId), "site", limit] as const,
    queryFn: () => apiFetch<ActorRunListItem[]>(`${base(siteId)}/runs?limit=${limit}`),
    enabled: validSite(siteId),
    refetchInterval: (query) => (hasActiveRun(query.state.data) ? RUNS_ACTIVE_POLL_MS : false),
  });
}

export function useScheduleRuns(siteId: number, scheduleId: number) {
  return useQuery({
    queryKey: [...actorKeys.runs(siteId), "schedule", scheduleId] as const,
    queryFn: () => apiFetch<ActorRun[]>(`${base(siteId)}/schedules/${scheduleId}/runs`),
    enabled: validSite(siteId) && Number.isFinite(scheduleId),
    refetchInterval: (query) => (hasActiveRun(query.state.data) ? RUNS_ACTIVE_POLL_MS : false),
  });
}

export function useActorFailures(siteId: number) {
  return useQuery({
    queryKey: actorKeys.failures(siteId),
    queryFn: () => apiFetch<ActorFailure[]>(`${base(siteId)}/failures`),
    enabled: validSite(siteId),
    refetchInterval: FAILURES_POLL_MS,
  });
}

type Key = readonly unknown[];

/** A mutation that refreshes the given queries once it succeeds. */
function useActorMutation<TArg, TResult>(
  siteId: number,
  request: (arg: TArg) => Promise<TResult>,
  keys: (siteId: number) => Key[],
) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: request,
    onSuccess: () => invalidateAll(queryClient, keys(siteId)),
  });
}

function invalidateAll(queryClient: QueryClient, keys: Key[]) {
  return Promise.all(keys.map((queryKey) => queryClient.invalidateQueries({ queryKey })));
}

const instanceKeys = (s: number): Key[] => [actorKeys.instances(s)];
// Deleting an instance also deletes its schedules and run history.
const instanceGoneKeys = (s: number): Key[] => [actorKeys.all(s)];
const scheduleKeys = (s: number): Key[] => [actorKeys.schedules(s), actorKeys.runs(s), actorKeys.failures(s)];
const runKeys = (s: number): Key[] => [actorKeys.runs(s), actorKeys.failures(s)];

export function useCreateInstance(siteId: number) {
  return useActorMutation(
    siteId,
    (body: InstanceCreateBody) =>
      apiFetch<ActorInstance>(`${base(siteId)}/instances`, { method: "POST", body: JSON.stringify(body) }),
    instanceKeys,
  );
}

export function useUpdateInstance(siteId: number) {
  return useActorMutation(
    siteId,
    ({ id, ...patch }: { id: number } & InstancePatchBody) =>
      apiFetch<ActorInstance>(`${base(siteId)}/instances/${id}`, { method: "PATCH", body: JSON.stringify(patch) }),
    instanceKeys,
  );
}

const signInResetKeys = (s: number): Key[] => [actorKeys.instances(s), actorKeys.failures(s)];

/** Clears the stored Actron token so the actor starts a new sign-in. */
export function useResetSignIn(siteId: number) {
  return useActorMutation(
    siteId,
    ({ instanceId }: { instanceId: number }) =>
      apiFetch<ActorInstance>(`${base(siteId)}/instances/${instanceId}/sign-in/reset`, { method: "POST" }),
    signInResetKeys,
  );
}

export function useDeleteInstance(siteId: number) {
  return useActorMutation(
    siteId,
    (instanceId: number) => apiFetch<void>(`${base(siteId)}/instances/${instanceId}`, { method: "DELETE" }),
    instanceGoneKeys,
  );
}

export function useCreateSchedule(siteId: number) {
  return useActorMutation(
    siteId,
    (body: ScheduleBody) =>
      apiFetch<ActorSchedule>(`${base(siteId)}/schedules`, { method: "POST", body: JSON.stringify(body) }),
    scheduleKeys,
  );
}

export function useUpdateSchedule(siteId: number) {
  return useActorMutation(
    siteId,
    ({ id, ...patch }: { id: number } & SchedulePatchBody) =>
      apiFetch<ActorSchedule>(`${base(siteId)}/schedules/${id}`, { method: "PATCH", body: JSON.stringify(patch) }),
    scheduleKeys,
  );
}

export function useDeleteSchedule(siteId: number) {
  return useActorMutation(
    siteId,
    (scheduleId: number) => apiFetch<void>(`${base(siteId)}/schedules/${scheduleId}`, { method: "DELETE" }),
    scheduleKeys,
  );
}

export function useTestSchedule(siteId: number) {
  return useActorMutation(
    siteId,
    (scheduleId: number) => apiFetch<ActorRun>(`${base(siteId)}/schedules/${scheduleId}/test`, { method: "POST" }),
    runKeys,
  );
}
