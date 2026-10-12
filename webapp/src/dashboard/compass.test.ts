import { describe, expect, it } from "vitest";
import { compassDirection } from "./compass";

describe("compassDirection", () => {
  it("maps 0 degrees to N", () => {
    expect(compassDirection(0)).toBe("N");
  });

  it("maps 45 degrees to NE", () => {
    expect(compassDirection(45)).toBe("NE");
  });

  it("maps 90 degrees to E", () => {
    expect(compassDirection(90)).toBe("E");
  });

  it("maps 180 degrees to S", () => {
    expect(compassDirection(180)).toBe("S");
  });

  it("maps 270 degrees to W", () => {
    expect(compassDirection(270)).toBe("W");
  });

  it("rounds a value inside a segment to its nearest compass point", () => {
    expect(compassDirection(10)).toBe("N");
    expect(compassDirection(20)).toBe("NNE");
  });

  it("wraps values at or near 360 back around to N", () => {
    expect(compassDirection(360)).toBe("N");
    expect(compassDirection(355)).toBe("N");
  });

  it("normalizes negative degrees", () => {
    expect(compassDirection(-45)).toBe("NW");
  });
});
