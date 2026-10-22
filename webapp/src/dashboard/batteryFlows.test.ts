import { describe, expect, it } from "vitest";
import { buildBatteryChartData, splitBatteryFlows } from "./batteryFlows";

const RANGE = { from: new Date("2026-10-08T07:00:00Z"), to: new Date("2026-10-08T07:15:00Z") };
const reading = (ts: string, value: number) => ({ ts, metric: "x", value });
const empty = { soc: [], charge: [], discharge: [], production: [], consumption: [], gridExport: [] };

describe("splitBatteryFlows", () => {
  it("charges from the solar surplus first, then the grid", () => {
    const flows = splitBatteryFlows(3000, 0, 4000, 2500, 0);
    expect(flows.solarToBattery).toBe(1500);
    expect(flows.gridToBattery).toBe(1500);
  });

  it("sends discharge to the grid up to the export, and the rest to the house", () => {
    const flows = splitBatteryFlows(0, 3000, 0, 1000, 800);
    expect(flows.batteryToGrid).toBe(800);
    expect(flows.batteryToHouse).toBe(2200);
  });
});

describe("buildBatteryChartData", () => {
  it("zero-fills every 5-minute bucket and leaves SOC change null without data", () => {
    const data = buildBatteryChartData(empty, RANGE);
    expect(data).toHaveLength(3);
    expect(data[0].socChangePct).toBeNull();
    expect(data[0].solarToBatteryKw).toBe(0);
  });

  it("shows SOC change against the previous bucket's last reading", () => {
    const data = buildBatteryChartData(
      {
        ...empty,
        soc: [
          reading("2026-10-08T07:01:00Z", 50),
          reading("2026-10-08T07:04:00Z", 52),
          reading("2026-10-08T07:09:00Z", 51),
        ],
      },
      RANGE,
    );
    expect(data.map((p) => p.socChangePct)).toEqual([null, -1, null]);
  });

  it("averages kW over the bucket's five minutes", () => {
    const data = buildBatteryChartData(
      {
        ...empty,
        charge: [reading("2026-10-08T07:00:00Z", 5000)],
        production: [reading("2026-10-08T07:00:00Z", 5000)],
      },
      RANGE,
    );
    // One minute at 5 kW over five minutes averages 1 kW.
    expect(data[0].solarToBatteryKw).toBeCloseTo(1);
    expect(data[0].gridToBatteryKw).toBe(0);
  });
});
