import { describe, expect, it } from "vitest";
import type { TotalsRow } from "../dashboard/useHistoricalData";
import { summarizeTotals } from "./totalsSummary";

function row(overrides: Partial<TotalsRow>): TotalsRow {
  return {
    start: "2026-11-01T00:00:00Z",
    date: "2026-11-01",
    hours: 1,
    cost_c: 0,
    avg_import_price_c: null,
    avg_export_price_c: null,
    production_kwh: 0,
    consumption_kwh: 0,
    solar_to_battery_kwh: 0,
    grid_to_battery_kwh: 0,
    grid_import_kwh: 0,
    grid_export_kwh: 0,
    battery_charge_kwh: 0,
    battery_discharge_kwh: 0,
    battery_soc_pct: null,
    demand_charge_c: null,
    demand_charge_estimated: false,
    ...overrides,
  };
}

describe("summarizeTotals", () => {
  it("sums energy, cost and demand charge", () => {
    const summary = summarizeTotals([
      row({ cost_c: 100, production_kwh: 4, demand_charge_c: 50, demand_charge_estimated: true }),
      row({ cost_c: -30, production_kwh: 6, demand_charge_c: 25 }),
    ]);
    expect(summary).toMatchObject({ cost_c: 70, production_kwh: 10, demand_charge_c: 75, demand_charge_estimated: true });
  });

  it("averages the prices and skips rows without one", () => {
    const summary = summarizeTotals([
      row({ avg_import_price_c: 20, avg_export_price_c: null }),
      row({ avg_import_price_c: 30, avg_export_price_c: 8 }),
      row({ avg_import_price_c: null, avg_export_price_c: 4 }),
    ]);
    expect(summary.avg_import_price_c).toBe(25);
    expect(summary.avg_export_price_c).toBe(6);
  });

  it("leaves prices and demand charge null when no row has one", () => {
    const summary = summarizeTotals([row({})]);
    expect(summary).toMatchObject({ avg_import_price_c: null, avg_export_price_c: null, demand_charge_c: null });
  });
});
