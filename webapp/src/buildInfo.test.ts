import { describe, expect, it } from "vitest";
import { commitUrl } from "./buildInfo";

describe("commitUrl", () => {
  it("links a hex commit to GitHub", () => {
    expect(commitUrl("abc1234")).toBe("https://github.com/sschwetz/wattwise/commit/abc1234");
  });

  it("returns null for non-commit values", () => {
    expect(commitUrl("unknown")).toBeNull();
    expect(commitUrl("dev")).toBeNull();
  });
});
