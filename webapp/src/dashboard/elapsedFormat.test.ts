import { describe, expect, it } from "vitest";
import { formatElapsed } from "./elapsedFormat";

describe("formatElapsed", () => {
  it.each([
    [0, "0 sec"],
    [999, "0 sec"],
    [30_000, "30 sec"],
    [59_999, "59 sec"],
    [60_000, "1 min"],
    [179_000, "2 min"],
    [3_599_000, "59 min"],
    [3_600_000, "1 hour"],
    [3 * 3_600_000, "3 hours"],
    [26 * 3_600_000, "26 hours"],
  ])("formats %i ms as %s", (ms, expected) => {
    expect(formatElapsed(ms)).toBe(expected);
  });

  it("never goes negative when the clock steps backwards", () => {
    expect(formatElapsed(-5_000)).toBe("0 sec");
  });
});
