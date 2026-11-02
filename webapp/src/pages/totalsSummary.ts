import type { TotalsRow } from "../dashboard/useHistoricalData";

export interface TotalsSummary {
  cost_c: number;
  production_kwh: number;
  consumption_kwh: number;
  solar_to_battery_kwh: number;
  grid_to_battery_kwh: number;
  demand_charge_c: number | null;
  demand_charge_estimated: boolean;
  avg_import_price_c: number | null;
  avg_export_price_c: number | null;
}

const sum = (values: number[]) => values.reduce((total, value) => total + value, 0);

/** The mean of the non-null values, or null when there are none. */
function mean(values: (number | null)[]): number | null {
  const present = values.filter((value): value is number => value !== null);
  return present.length === 0 ? null : sum(present) / present.length;
}

/** The footer row: every column summed except the two prices, which are averaged. */
export function summarizeTotals(rows: TotalsRow[]): TotalsSummary {
  const demand = rows.flatMap((row) => (row.demand_charge_c === null ? [] : [row.demand_charge_c]));
  return {
    cost_c: sum(rows.map((row) => row.cost_c)),
    production_kwh: sum(rows.map((row) => row.production_kwh)),
    consumption_kwh: sum(rows.map((row) => row.consumption_kwh)),
    solar_to_battery_kwh: sum(rows.map((row) => row.solar_to_battery_kwh)),
    grid_to_battery_kwh: sum(rows.map((row) => row.grid_to_battery_kwh)),
    demand_charge_c: demand.length === 0 ? null : sum(demand),
    demand_charge_estimated: rows.some((row) => row.demand_charge_estimated),
    avg_import_price_c: mean(rows.map((row) => row.avg_import_price_c)),
    avg_export_price_c: mean(rows.map((row) => row.avg_export_price_c)),
  };
}
