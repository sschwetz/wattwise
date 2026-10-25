import { type ChartColors, SWATCH_COLORS } from "./chartStyle";

/** The dashboard's one sign convention, used everywhere a number means "cost
 * vs. credit" or "import vs. export": positive -> danger (red), negative ->
 * flow (green), exactly zero -> neutral (each call site picks its own neutral
 * color/class, since "zero" renders differently as a Tile accent vs. a raw
 * chart color). */
export function signAccent(value: number): "danger" | "flow" | undefined {
  if (value > 0) {
    return "danger";
  }
  if (value < 0) {
    return "flow";
  }
  return undefined;
}

/** Orange while importing (costing money), green while exporting (earning money) --
 * the same convention as the "Grid import"/"Grid export" tiles' text color.
 * Exactly zero (e.g. a not-yet-happened bucket) is neutral, not orange, since
 * orange would otherwise misread as "importing". */
export function gridBarColor(value: number, colors: ChartColors = SWATCH_COLORS): string {
  if (value > 0) {
    return colors.gridImport;
  }
  return value < 0 ? colors.gridExport : colors.line;
}

/** Same orange/green convention as gridBarColor, as a raw CSS color for inline
 * styling (the chart tooltip's cost row). */
export function costTextColor(value: number): string {
  if (value === 0) {
    return SWATCH_COLORS.inkMuted;
  }
  return gridBarColor(value);
}
