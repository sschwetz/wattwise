import { useMemo, useSyncExternalStore } from "react";

// Fixed legend/tooltip swatch colors, independent of any series' recharts
// `fill`/`stroke` -- the "Grid" bar has no single fill of its own (each bar's
// color comes from a per-point Cell instead, since it swings between import
// and export), so relying on recharts' own item color would leave the
// tooltip/legend with no color of their own to fall back on.
export const SWATCH_COLORS = {
  gridImport: "var(--chart-orange)",
  gridExport: "var(--chart-aqua)",
  cost: "var(--chart-blue)",
  batterySoc: "var(--chart-soc)",
  solarToBattery: "var(--chart-yellow)",
  gridToBattery: "var(--chart-magenta)",
  batteryToHouse: "var(--chart-blue)",
  batteryToGrid: "var(--chart-aqua)",
  spikePossible: "var(--chart-yellow)",
  spike: "var(--chart-red)",
  inkMuted: "var(--color-ink-muted)",
  line: "var(--color-line)",
} as const;

/** The same palette as SWATCH_COLORS with each var() resolved to a concrete color. */
export type ChartColors = Record<keyof typeof SWATCH_COLORS, string>;

export const ZOOMABLE_CHART_STYLE = { userSelect: "none", cursor: "crosshair" } as const;

/** The "today" charts on the dashboard share one hover position. */
export const TODAY_CHARTS_SYNC_ID = "today-charts";

const CSS_VAR = /^var\((--[\w-]+)\)$/;

const darkQuery = typeof window === "undefined" ? undefined : window.matchMedia?.("(prefers-color-scheme: dark)");
const resolvedColors = new Map<string, string>();
// Registered at load, before any chart subscribes, so the cache is already
// empty when the charts re-render on a scheme change.
darkQuery?.addEventListener("change", () => resolvedColors.clear());

function svgColor(value: string): string {
  const name = CSS_VAR.exec(value)?.[1];
  if (!name) {
    return value;
  }
  let resolved = resolvedColors.get(name);
  if (resolved === undefined) {
    resolved = getComputedStyle(document.documentElement).getPropertyValue(name).trim();
    if (resolved) {
      resolvedColors.set(name, resolved);
    }
  }
  return resolved || value;
}

function subscribeColorScheme(onChange: () => void) {
  darkQuery?.addEventListener("change", onChange);
  return () => darkQuery?.removeEventListener("change", onChange);
}

function useColorScheme(): boolean {
  return useSyncExternalStore(subscribeColorScheme, () => darkQuery?.matches ?? false);
}

/** SWATCH_COLORS with every var() resolved, for anything recharts draws.
 * Safari ignores var() in SVG presentation attributes (`stroke`/`fill`), so
 * shapes given a var() render in the default color. HTML styles such as the
 * legend swatches can keep using SWATCH_COLORS. Re-resolves, and re-renders
 * the calling chart, when the OS light/dark setting changes. */
export function useChartColors(): ChartColors {
  const dark = useColorScheme();
  // `dark` isn't read in the body: it only invalidates the memo when the scheme flips.
  // oxlint-disable-next-line react-hooks/exhaustive-deps
  return useMemo(() => mapColors(SWATCH_COLORS), [dark]);
}

function mapColors(colors: typeof SWATCH_COLORS): ChartColors {
  return Object.fromEntries(Object.entries(colors).map(([key, value]) => [key, svgColor(value)])) as ChartColors;
}

/** The axis styling every chart shares. */
export function axisProps(colors: ChartColors) {
  return {
    stroke: colors.inkMuted,
    fontSize: 12,
    tickLine: false,
    axisLine: false,
  } as const;
}

/** Props for a y axis with a rotated unit label, on the left or right edge.
 * Spread onto <YAxis> (recharts needs the axis itself as the chart's child). */
export function yAxisProps(colors: ChartColors, unit: string, side: "left" | "right" = "left") {
  return {
    ...axisProps(colors),
    width: 48,
    ...(side === "right" ? { orientation: "right" as const } : {}),
    label: {
      value: unit,
      angle: -90,
      position: side === "left" ? ("insideLeft" as const) : ("insideRight" as const),
      style: { fill: colors.inkMuted, fontSize: 12, textAnchor: "middle" as const },
    },
  };
}
