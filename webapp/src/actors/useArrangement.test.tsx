import type { DragEndEvent } from "@dnd-kit/core";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, renderHook, waitFor } from "@testing-library/react";
import type { ReactNode } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { apiFetch } from "../api/client";
import type { ActorSchedule, ScheduleLayoutItem } from "./types";
import { useArrangement } from "./useArrangement";

vi.mock("../api/client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../api/client")>();
  return { ...actual, apiFetch: vi.fn() };
});

const schedules = [1, 2, 3].map((id) => ({ id }) as ActorSchedule);
const sched = (id: number): ScheduleLayoutItem => ({ type: "schedule", id });
const drag = (active: string, over: string | null) =>
  ({ active: { id: active }, over: over === null ? null : { id: over } }) as unknown as DragEndEvent;

function serve(layout: "fail" | "never" | ScheduleLayoutItem[]) {
  vi.mocked(apiFetch).mockImplementation(async (_path: string, init?: RequestInit) => {
    if (init?.method === "PUT") return { items: JSON.parse(String(init.body)).items };
    if (layout === "fail") throw new Error("down");
    if (layout === "never") return new Promise(() => {});
    return { items: layout };
  });
}

function setup() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  const wrapper = ({ children }: { children: ReactNode }) => <QueryClientProvider client={client}>{children}</QueryClientProvider>;
  return renderHook(() => useArrangement(7, schedules), { wrapper });
}

const puts = () => vi.mocked(apiFetch).mock.calls.filter(([, init]) => init?.method === "PUT");
const putItems = (n: number) => JSON.parse(String(puts()[n][1]?.body)).items;

beforeEach(() => {
  vi.mocked(apiFetch).mockReset();
});

describe("useArrangement drop", () => {
  it("sends the whole new order when a row is dropped on another", async () => {
    serve([sched(1), sched(2), sched(3)]);
    const { result } = setup();
    await waitFor(() => expect(result.current.ready).toBe(true));
    act(() => result.current.drop(drag("s:3", "s:1")));
    await waitFor(() => expect(puts()).toHaveLength(1));
    expect(putItems(0)).toEqual([sched(3), sched(1), sched(2)]);
  });

  it("sends nothing for a drop outside the list or on itself", async () => {
    serve([sched(1), sched(2), sched(3)]);
    const { result } = setup();
    await waitFor(() => expect(result.current.ready).toBe(true));
    await act(async () => {
      result.current.drop(drag("s:2", null));
      result.current.drop(drag("s:2", "s:2"));
    });
    expect(puts()).toHaveLength(0);
  });
});

describe("useArrangement before the saved order is known", () => {
  it.each(["never", "fail"] as const)("sends no edit while the saved order is %s, so it cannot overwrite it", async (state) => {
    serve(state);
    const { result } = setup();
    if (state === "fail") await waitFor(() => expect(result.current.loadFailed).toBe(true));
    expect(result.current.ready).toBe(false);
    await act(async () => {
      result.current.addSeparator();
      result.current.drop(drag("s:3", "s:1"));
    });
    expect(puts()).toHaveLength(0);
  });
});
