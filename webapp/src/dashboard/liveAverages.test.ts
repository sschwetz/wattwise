import { describe, expect, it } from "vitest";
import { averageLiveReadings } from "./liveAverages";

const at = (minutes: number, seconds = 0) => new Date(Date.UTC(2026, 0, 1, 0, minutes, seconds)).toISOString();
const reading = (ts: string, metric: string, value: number) => ({ ts, metric, value });
const valueOf = (readings: ReturnType<typeof averageLiveReadings>, metric: string) =>
  readings.find((r) => r.metric === metric)?.value;

describe("averageLiveReadings", () => {
  const now = Date.UTC(2026, 0, 1, 0, 7, 30);

  it("averages the current clock-aligned 5-minute bucket so far", () => {
    const result = averageLiveReadings(
      [
        // previous bucket (0:00-0:05): ignored
        reading(at(4, 59), "production_w", 9999),
        reading(at(5), "production_w", 100),
        reading(at(6), "production_w", 200),
        reading(at(7), "production_w", 300),
        reading(at(7), "consumption_w", 60),
      ],
      now,
    );
    expect(valueOf(result, "production_w")).toBe(200);
    expect(valueOf(result, "consumption_w")).toBe(20);
  });

  it("restarts at the bucket boundary and falls back to the latest reading until a new poll lands", () => {
    const result = averageLiveReadings([reading(at(4, 59), "production_w", 123)], Date.UTC(2026, 0, 1, 0, 5, 10));
    expect(valueOf(result, "production_w")).toBe(123);
  });

  it("counts poll cycles without an import row as 0 W", () => {
    const result = averageLiveReadings(
      [
        reading(at(5, 30), "production_w", 10),
        reading(at(5, 30), "grid_import_w", 400),
        reading(at(6), "production_w", 10),
        reading(at(6), "grid_export_w", 300),
      ],
      now,
    );
    expect(valueOf(result, "grid_import_w")).toBe(200);
  });

  it("leaves other metrics unchanged", () => {
    const result = averageLiveReadings(
      [reading(at(5, 30), "grid_export_w", 100), reading(at(6), "grid_export_w", 300), reading(at(6), "battery_soc_pct", 80)],
      now,
    );
    // Passed through as-is; the tile grid picks the newest row per metric.
    expect(result.filter((r) => r.metric === "grid_export_w")).toHaveLength(2);
    expect(valueOf(result, "battery_soc_pct")).toBe(80);
  });
});
