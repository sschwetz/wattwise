import { act, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { configureApiClient } from "../api/client";
import { toWebSocketUrl } from "../api/config";
import { FakeWebSocket } from "./fakeWebSocket";
import { useLiveStream } from "./useLiveStream";

const energyMsg = (ts: string, value = 5) => ({ kind: "energy", ts, values: { production_w: value } });
const last = () => FakeWebSocket.instances[FakeWebSocket.instances.length - 1];

describe("useLiveStream", () => {
  const original = globalThis.WebSocket;

  beforeEach(() => {
    vi.useFakeTimers();
    FakeWebSocket.instances = [];
    globalThis.WebSocket = FakeWebSocket as unknown as typeof WebSocket;
    localStorage.setItem("access_token", "tok");
    configureApiClient(() => localStorage.getItem("access_token"), () => undefined);
  });

  afterEach(() => {
    vi.useRealTimers();
    globalThis.WebSocket = original;
    localStorage.clear();
  });

  it("connects and sends the token on open", () => {
    renderHook(() => useLiveStream(7));
    expect(FakeWebSocket.instances).toHaveLength(1);
    expect(last().url).toBe(toWebSocketUrl("/api/v1/sites/7/live"));
    act(() => last().open());
    expect(JSON.parse(last().sent[0])).toEqual({ token: "tok" });
  });

  it("does not connect without a token", () => {
    localStorage.clear();
    renderHook(() => useLiveStream(7));
    expect(FakeWebSocket.instances).toHaveLength(0);
  });

  it("routes messages by kind", () => {
    const { result } = renderHook(() => useLiveStream(7));
    act(() => last().open());
    act(() => last().message(energyMsg(new Date().toISOString())));
    act(() => last().message({ kind: "weather", ts: new Date().toISOString(), values: { temp_c: 20 } }));
    expect(result.current.energy.map((r) => r.metric)).toEqual(["production_w"]);
    expect(result.current.weather.map((r) => r.metric)).toEqual(["temp_c"]);
  });

  it("drops a value 15 s after it arrived", () => {
    const { result } = renderHook(() => useLiveStream(7));
    act(() => last().open());
    act(() => last().message(energyMsg(new Date().toISOString())));
    expect(result.current.energy).toHaveLength(1);
    act(() => {
      vi.advanceTimersByTime(14_999);
    });
    expect(result.current.energy).toHaveLength(1);
    act(() => {
      vi.advanceTimersByTime(1);
    });
    expect(result.current.energy).toHaveLength(0);
  });

  it("stops re-rendering once values have expired", () => {
    let renders = 0;
    const { result } = renderHook(() => {
      renders += 1;
      return useLiveStream(7);
    });
    act(() => last().open());
    act(() => last().message(energyMsg(new Date().toISOString())));
    act(() => {
      vi.advanceTimersByTime(60_000);
    });
    expect(result.current.energy).toHaveLength(0);
    expect(renders).toBeLessThan(10);
    expect(vi.getTimerCount()).toBe(0);
  });

  it("reconnects with backoff and resets after a message", () => {
    renderHook(() => useLiveStream(7));
    act(() => last().closeWith(1006));
    act(() => {
      vi.advanceTimersByTime(999);
    });
    expect(FakeWebSocket.instances).toHaveLength(1);
    act(() => {
      vi.advanceTimersByTime(1);
    });
    expect(FakeWebSocket.instances).toHaveLength(2);
    act(() => last().closeWith(1006));
    act(() => {
      vi.advanceTimersByTime(1999);
    });
    expect(FakeWebSocket.instances).toHaveLength(2);
    act(() => {
      vi.advanceTimersByTime(1);
    });
    expect(FakeWebSocket.instances).toHaveLength(3);
    act(() => last().open());
    act(() => last().message(energyMsg(new Date().toISOString())));
    act(() => last().closeWith(1006));
    act(() => {
      vi.advanceTimersByTime(1000);
    });
    expect(FakeWebSocket.instances).toHaveLength(4);
  });

  it("does not reconnect after 1008", () => {
    renderHook(() => useLiveStream(7));
    act(() => last().closeWith(1008));
    act(() => {
      vi.advanceTimersByTime(60_000);
    });
    expect(FakeWebSocket.instances).toHaveLength(1);
  });

  it("closes on unmount and cancels a pending reconnect", () => {
    const { unmount } = renderHook(() => useLiveStream(7));
    const first = last();
    unmount();
    expect(first.closed).toBe(true);

    const second = renderHook(() => useLiveStream(7));
    act(() => last().closeWith(1006));
    const count = FakeWebSocket.instances.length;
    second.unmount();
    act(() => {
      vi.advanceTimersByTime(60_000);
    });
    expect(FakeWebSocket.instances).toHaveLength(count);
  });
});
