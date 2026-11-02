import type { TotalsRow } from "../dashboard/useHistoricalData";

export type SortKey = keyof Pick<
  TotalsRow,
  | "start"
  | "cost_c"
  | "avg_import_price_c"
  | "avg_export_price_c"
  | "production_kwh"
  | "consumption_kwh"
  | "solar_to_battery_kwh"
  | "grid_to_battery_kwh"
  | "demand_charge_c"
>;
export type SortDirection = "asc" | "desc";

export interface SortState {
  key: SortKey;
  direction: SortDirection;
}

/** Time, newest first. */
export const DEFAULT_SORT: SortState = { key: "start", direction: "desc" };

/** The state after a click on `key`: a new column starts descending, the same one flips. */
export function nextSort(previous: SortState, key: SortKey): SortState {
  return { key, direction: previous.key === key && previous.direction === "desc" ? "asc" : "desc" };
}

/** Rows ordered by the sort; a row with no value for the column sorts last either way. */
export function sortTotals(rows: TotalsRow[], { key, direction }: SortState): TotalsRow[] {
  const sign = direction === "asc" ? 1 : -1;
  return [...rows].sort((a, b) => {
    const left = a[key];
    const right = b[key];
    if (left === null || right === null) {
      return left === right ? 0 : left === null ? 1 : -1;
    }
    return left === right ? 0 : (left < right ? -1 : 1) * sign;
  });
}
