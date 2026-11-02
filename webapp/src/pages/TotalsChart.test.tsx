import { render } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { TotalsRow } from "../dashboard/useHistoricalData";
import { TotalsChart } from "./TotalsChart";

let clickHandler: ((state: { activeTooltipIndex?: number }) => void) | undefined;

vi.mock("recharts", async (importOriginal) => {
  const actual = await importOriginal<typeof import("recharts")>();
  return {
    ...actual,
    ResponsiveContainer: ({ children }: { children: React.ReactNode }) => <>{children}</>,
    ComposedChart: (props: { onClick?: typeof clickHandler }) => {
      clickHandler = props.onClick;
      return null;
    },
  };
});

function row(date: string): TotalsRow {
  return {
    start: `${date}T00:00:00Z`,
    date,
    hours: 24,
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
  };
}

describe("TotalsChart click", () => {
  const rows = [row("2026-10-01"), row("2026-10-02")];

  beforeEach(() => {
    clickHandler = undefined;
  });

  it("selects the row under the click for day and hour charts", () => {
    for (const bucket of ["day", "hour"] as const) {
      const onSelectRow = vi.fn();
      render(<TotalsChart rows={rows} bucket={bucket} onSelectRow={onSelectRow} />);
      clickHandler?.({ activeTooltipIndex: 1 });
      expect(onSelectRow).toHaveBeenCalledWith(rows[1]);
    }
  });

  it("ignores clicks that miss a row", () => {
    const onSelectRow = vi.fn();
    render(<TotalsChart rows={rows} bucket="day" onSelectRow={onSelectRow} />);
    clickHandler?.({});
    expect(onSelectRow).not.toHaveBeenCalled();
  });

  it("does nothing on the minute chart", () => {
    const onSelectRow = vi.fn();
    render(<TotalsChart rows={rows} bucket="minute" onSelectRow={onSelectRow} />);
    expect(clickHandler).toBeUndefined();
  });
});
