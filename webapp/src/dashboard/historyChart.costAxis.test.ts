import { describe, expect, it } from "vitest";
import { buildCostAxis } from "./historyChart";

const pts = (...costs: number[]) => costs.map((costDollars, i) => ({ ts: i, gridKwh: 0, costDollars }));

describe("buildCostAxis", () => {
  it("uses a 1c step for the 0.024 peak in the spike screenshot", () => {
    const { ticks, domain } = buildCostAxis(pts(0, 0.024, 0.01));
    expect(ticks).toEqual([0, 0.01, 0.02, 0.03]);
    expect(domain).toEqual([0, 0.03]);
  });

  it("returns a single step of range when all costs are zero", () => {
    const { ticks, domain } = buildCostAxis(pts(0, 0, 0));
    expect(ticks).toEqual([0, 0.01]);
    expect(domain).toEqual([0, 0.01]);
  });

  it("returns a single step of range for empty data", () => {
    expect(buildCostAxis([]).ticks).toEqual([0, 0.01]);
  });

  it("includes negative costs from exports", () => {
    const { ticks, domain } = buildCostAxis(pts(-0.04, 0.02));
    expect(domain).toEqual([-0.04, 0.02]);
    expect(ticks).toEqual([-0.04, -0.02, 0, 0.02]);
  });

  it("widens the step so a large range has at most 5 intervals", () => {
    const { ticks, domain } = buildCostAxis(pts(0, 7.3));
    expect(domain).toEqual([0, 8]);
    expect(ticks).toEqual([0, 2, 4, 6, 8]);
    expect(ticks.length - 1).toBeLessThanOrEqual(5);
  });

  it("never produces ticks with float error", () => {
    const { ticks } = buildCostAxis(pts(0, 0.29));
    for (const t of ticks) expect(Math.round(t * 100) / 100).toBe(t);
  });
});
