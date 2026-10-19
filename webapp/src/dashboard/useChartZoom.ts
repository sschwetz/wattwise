import { useCallback, useState } from "react";
import type { DateRange } from "./useHistoricalData";

/** A zoom window held by a page and shared by the charts on it; null `range`
 * means each chart shows its own full range. */
export interface ChartZoom {
  range: DateRange | null;
  set: (range: DateRange | null) => void;
}

/** The x values (timestamps) of a drag-selection in progress. */
export interface ZoomSelection {
  x1: number;
  x2: number;
}

interface ChartMouseState {
  activeLabel?: string | number;
}

function labelToTs(state: ChartMouseState): number | null {
  if (state.activeLabel === undefined) {
    return null;
  }
  const ts = Number(state.activeLabel);
  return Number.isFinite(ts) ? ts : null;
}

/**
 * Drag-to-zoom for a recharts chart whose x axis is a timestamp. Spread
 * `handlers` onto the chart and render a ReferenceArea from `selection`.
 * `onZoom` gets the first and last selected timestamps (ascending) when the
 * mouse is released over a different point than it was pressed on; leaving
 * the chart mid-drag cancels the selection.
 */
export function useChartZoom(onZoom: (fromTs: number, toTs: number) => void) {
  const [anchor, setAnchor] = useState<number | null>(null);
  const [current, setCurrent] = useState<number | null>(null);

  const reset = useCallback(() => {
    setAnchor(null);
    setCurrent(null);
  }, []);

  const handlers = {
    onMouseDown: (state: ChartMouseState) => {
      const ts = labelToTs(state);
      if (ts !== null) {
        setAnchor(ts);
        setCurrent(ts);
      }
    },
    onMouseMove: (state: ChartMouseState) => {
      const ts = labelToTs(state);
      if (anchor !== null && ts !== null) {
        setCurrent(ts);
      }
    },
    onMouseUp: () => {
      if (anchor !== null && current !== null && anchor !== current) {
        onZoom(Math.min(anchor, current), Math.max(anchor, current));
      }
      reset();
    },
    onMouseLeave: reset,
  };

  const selection: ZoomSelection | null =
    anchor !== null && current !== null && anchor !== current ? { x1: anchor, x2: current } : null;
  return { handlers, selection };
}
