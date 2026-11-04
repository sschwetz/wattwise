import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, renderHook, waitFor } from "@testing-library/react";
import type { ReactNode } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ApiError } from "../api/client";
import type { ActorInstance, ActorRun } from "./types";
import {
  actorKeys,
  FAILURES_POLL_MS,
  INSTANCES_SIGN_IN_POLL_MS,
  useActorFailures,
  useActorInstances,
  useCreateSchedule,
  useDeleteInstance,
  useRuns,
  useTestSchedule,
} from "./useActors";

const fetchMock = vi.fn();

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}

function makeClient() {
  return new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
}

function wrapperFor(client: QueryClient) {
  return ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={client}>{children}</QueryClientProvider>
  );
}

function instance(auth_state: ActorInstance["auth_state"]): ActorInstance {
  return {
    id: 1, site_id: 7, provider: "actron_neo", name: "Unit", enabled: true, capabilities: null,
    capabilities_updated_at: null, latest_readings: null, last_seen_at: null, auth_state,
  };
}

const run: ActorRun = {
  id: 9, schedule_id: 3, kind: "test", trigger_at: "2026-11-05T00:00:00+00:00", started_at: null,
  finished_at: null, outcome: "pending", message: null, attempts: 0, parent_run_id: null, snapshot: null,
};

beforeEach(() => {
  fetchMock.mockReset();
  vi.stubGlobal("fetch", fetchMock);
});

afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

function urlOf(call: number): string {
  return String(fetchMock.mock.calls[call][0]);
}

describe("actor hooks", () => {
  it("useActorFailures refetches on an interval", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    fetchMock.mockImplementation(() => Promise.resolve(json([])));
    const { result } = renderHook(() => useActorFailures(7), { wrapper: wrapperFor(makeClient()) });
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(urlOf(0)).toContain("/sites/7/actors/failures");
    await act(async () => {
      await vi.advanceTimersByTimeAsync(FAILURES_POLL_MS + 100);
    });
    expect(fetchMock.mock.calls.length).toBeGreaterThanOrEqual(2);
  });

  it("useCreateSchedule posts the body and invalidates the schedules query", async () => {
    const client = makeClient();
    const invalidate = vi.spyOn(client, "invalidateQueries");
    fetchMock.mockResolvedValue(json({ id: 3 }, 201));
    const { result } = renderHook(() => useCreateSchedule(7), { wrapper: wrapperFor(client) });
    const body = {
      instance_id: 1,
      name: "Evening",
      trigger: { type: "time" as const, time: "18:00", days_of_week: [0, 1] },
      action: { id: "turn_off", params: {} },
    };
    await act(async () => {
      await result.current.mutateAsync(body);
    });
    expect(urlOf(0)).toContain("/sites/7/actors/schedules");
    expect(fetchMock.mock.calls[0][1].method).toBe("POST");
    expect(JSON.parse(fetchMock.mock.calls[0][1].body)).toEqual(body);
    expect(invalidate).toHaveBeenCalledWith({ queryKey: actorKeys.schedules(7) });
  });

  it("errors surface as ApiError messages", async () => {
    fetchMock.mockResolvedValue(
      json({ code: "validation_error", message: "Window not found", detail: [{ loc: ["body"] }] }, 422),
    );
    const { result } = renderHook(() => useCreateSchedule(7), { wrapper: wrapperFor(makeClient()) });
    let caught: unknown;
    await act(async () => {
      caught = await result.current
        .mutateAsync({ instance_id: 1, name: "x", trigger: { type: "price_above", c_per_kwh: 5 }, action: { id: "a", params: {} } })
        .catch((e: unknown) => e);
    });
    expect(caught).toBeInstanceOf(ApiError);
    expect((caught as ApiError).status).toBe(422);
    expect((caught as ApiError).message).toBe("Window not found");
    await waitFor(() => expect(result.current.error).toBeInstanceOf(ApiError));
  });

  it("polls instances faster while one holds a user_code, then at the normal cadence", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const waiting = instance({ user_code: "AB12", verification_uri: "https://x", expires_at: "2026-11-05T01:00:00+00:00" });
    fetchMock.mockResolvedValueOnce(json([waiting])).mockImplementation(() => Promise.resolve(json([instance(null)])));
    const { result } = renderHook(() => useActorInstances(7), { wrapper: wrapperFor(makeClient()) });
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(fetchMock).toHaveBeenCalledTimes(1);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(INSTANCES_SIGN_IN_POLL_MS + 100);
    });
    expect(fetchMock).toHaveBeenCalledTimes(2);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(INSTANCES_SIGN_IN_POLL_MS * 2);
    });
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });

  it("useDeleteInstance hits the right URL and method", async () => {
    fetchMock.mockResolvedValue(new Response(null, { status: 204 }));
    const { result } = renderHook(() => useDeleteInstance(7), { wrapper: wrapperFor(makeClient()) });
    await act(async () => {
      await result.current.mutateAsync(4);
    });
    expect(urlOf(0)).toContain("/sites/7/actors/instances/4");
    expect(fetchMock.mock.calls[0][1].method).toBe("DELETE");
  });

  it("useTestSchedule returns the run", async () => {
    fetchMock.mockResolvedValue(json(run, 201));
    const { result } = renderHook(() => useTestSchedule(7), { wrapper: wrapperFor(makeClient()) });
    let got: ActorRun | undefined;
    await act(async () => {
      got = await result.current.mutateAsync(3);
    });
    expect(urlOf(0)).toContain("/sites/7/actors/schedules/3/test");
    expect(fetchMock.mock.calls[0][1].method).toBe("POST");
    expect(got).toEqual(run);
  });

  it("useRuns passes limit", async () => {
    fetchMock.mockResolvedValue(json([]));
    const { result } = renderHook(() => useRuns(7, 25), { wrapper: wrapperFor(makeClient()) });
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(urlOf(0)).toContain("/sites/7/actors/runs?limit=25");
  });

  it("is disabled when the site id is not a number", () => {
    renderHook(() => useActorInstances(Number.NaN), { wrapper: wrapperFor(makeClient()) });
    expect(fetchMock).not.toHaveBeenCalled();
  });
});
