// Fixed legend/tooltip swatch colors, independent of any series' recharts
// `fill`/`stroke` -- the "Grid" bar has no single fill of its own (each bar's
// color comes from a per-point Cell instead, since it swings between import
// and export), so relying on recharts' own item color would leave the
// tooltip/legend with no color of their own to fall back on.
export const SWATCH_COLORS = {
  gridImport: "var(--color-danger)",
  gridExport: "var(--color-flow)",
  cost: "var(--color-solar)",
} as const;

export const ZOOMABLE_CHART_STYLE = { userSelect: "none", cursor: "crosshair" } as const;

/** The two "today" charts on the dashboard share one hover position. */
export const TODAY_CHARTS_SYNC_ID = "today-charts";
