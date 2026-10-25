import { renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { SWATCH_COLORS, useChartColors } from "./chartStyle";

const VAR_NAME = /^var\((--[\w-]+)\)$/;

describe("useChartColors", () => {
  const names = Object.values(SWATCH_COLORS).map((value) => VAR_NAME.exec(value)![1]);

  beforeEach(() => {
    names.forEach((name) => document.documentElement.style.setProperty(name, "#123456"));
  });

  afterEach(() => {
    names.forEach((name) => document.documentElement.style.removeProperty(name));
  });

  it("resolves every palette var() to a concrete color, which Safari needs in SVG attributes", () => {
    const { result } = renderHook(() => useChartColors());

    for (const [key, value] of Object.entries(result.current)) {
      expect(value, key).toBe("#123456");
    }
  });
});
