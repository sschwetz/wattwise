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

const ACCENT_CSS_VAR: Record<"solar" | "flow" | "grid" | "danger", string> = {
  solar: "var(--color-solar)",
  flow: "var(--color-flow)",
  grid: "var(--color-grid)",
  danger: "var(--color-danger)",
};

/** Red while importing (costing money), green while exporting (earning money) --
 * the same convention as the "Grid import"/"Grid export" tiles' text color.
 * Exactly zero (e.g. a not-yet-happened bucket) is neutral, not red, since
 * red would otherwise misread as "importing". */
export function gridBarColor(value: number): string {
  const accent = signAccent(value);
  return accent ? ACCENT_CSS_VAR[accent] : "var(--color-line)";
}

/** Same red/green convention as gridBarColor, as a raw CSS color for inline
 * styling (the chart tooltip's cost row). */
export function costTextColor(value: number): string {
  const accent = signAccent(value);
  return accent ? ACCENT_CSS_VAR[accent] : "var(--color-ink-muted)";
}
