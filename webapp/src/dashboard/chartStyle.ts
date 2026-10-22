// Fixed legend/tooltip swatch colors, independent of any series' recharts
// `fill`/`stroke` -- the "Grid" bar has no single fill of its own (each bar's
// color comes from a per-point Cell instead, since it swings between import
// and export), so relying on recharts' own item color would leave the
// tooltip/legend with no color of their own to fall back on.
export const SWATCH_COLORS = {
  gridImport: "var(--color-danger)",
  gridExport: "var(--color-flow)",
  cost: "var(--color-solar)",
  batterySocChange: "var(--color-solar)",
  solarToBattery: "var(--color-flow)",
  gridToBattery: "var(--color-grid-charge)",
  batteryToHouse: "var(--color-grid)",
  batteryToGrid: "var(--color-flow)",
} as const;

export const ZOOMABLE_CHART_STYLE = { userSelect: "none", cursor: "crosshair" } as const;

/** The "today" charts on the dashboard share one hover position. */
export const TODAY_CHARTS_SYNC_ID = "today-charts";

/** The axis styling every chart shares. */
export const AXIS_PROPS = {
  stroke: "var(--color-ink-muted)",
  fontSize: 12,
  tickLine: false,
  axisLine: false,
} as const;

/** Props for a y axis with a rotated unit label, on the left or right edge.
 * Spread onto <YAxis> (recharts needs the axis itself as the chart's child). */
export function yAxisProps(unit: string, side: "left" | "right" = "left") {
  return {
    ...AXIS_PROPS,
    width: 48,
    ...(side === "right" ? { orientation: "right" as const } : {}),
    label: {
      value: unit,
      angle: -90,
      position: side === "left" ? ("insideLeft" as const) : ("insideRight" as const),
      style: { fill: "var(--color-ink-muted)", fontSize: 12, textAnchor: "middle" as const },
    },
  };
}
