import { act, renderHook } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { useChartZoom } from "./useChartZoom";

describe("useChartZoom", () => {
  it("reports an ascending range when dragged right to left", () => {
    const onZoom = vi.fn();
    const { result } = renderHook(() => useChartZoom(onZoom));

    act(() => result.current.handlers.onMouseDown({ activeLabel: 300 }));
    act(() => result.current.handlers.onMouseMove({ activeLabel: 100 }));
    expect(result.current.selection).toEqual({ x1: 300, x2: 100 });
    act(() => result.current.handlers.onMouseUp());

    expect(onZoom).toHaveBeenCalledWith(100, 300);
    expect(result.current.selection).toBeNull();
  });

  it("ignores a click without a drag", () => {
    const onZoom = vi.fn();
    const { result } = renderHook(() => useChartZoom(onZoom));

    act(() => result.current.handlers.onMouseDown({ activeLabel: 100 }));
    act(() => result.current.handlers.onMouseUp());

    expect(onZoom).not.toHaveBeenCalled();
  });

  it("cancels the selection when the mouse leaves the chart", () => {
    const onZoom = vi.fn();
    const { result } = renderHook(() => useChartZoom(onZoom));

    act(() => result.current.handlers.onMouseDown({ activeLabel: 100 }));
    act(() => result.current.handlers.onMouseMove({ activeLabel: 200 }));
    act(() => result.current.handlers.onMouseLeave());
    act(() => result.current.handlers.onMouseUp());

    expect(onZoom).not.toHaveBeenCalled();
    expect(result.current.selection).toBeNull();
  });
});
