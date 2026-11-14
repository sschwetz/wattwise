import { describe, expect, it } from "vitest";
import { EMPTY_LOCATION, locationPatch, locationText, parsePoint, pointText } from "./location";

describe("location helpers", () => {
  it("reads a site's location into the boxes, empty when it has none", () => {
    expect(locationText({ latitude: -33.87, longitude: 151.21 })).toEqual({ latitude: "-33.87", longitude: "151.21" });
    expect(locationText({ latitude: null, longitude: null })).toEqual(EMPTY_LOCATION);
  });

  it("rounds a picked point to five decimals", () => {
    expect(pointText({ latitude: -33.8688197, longitude: 151.2092955 })).toEqual({ latitude: "-33.86882", longitude: "151.2093" });
  });

  it("parses only a point in range", () => {
    expect(parsePoint({ latitude: "-33.8", longitude: "151.2" })).toEqual({ latitude: -33.8, longitude: 151.2 });
    expect(parsePoint({ latitude: "", longitude: "151.2" })).toBeNull();
    expect(parsePoint({ latitude: "91", longitude: "0" })).toBeNull();
    expect(parsePoint({ latitude: "0", longitude: "-181" })).toBeNull();
    expect(parsePoint({ latitude: "abc", longitude: "0" })).toBeNull();
  });

  it("builds the patch: unchanged, cleared, set or an error", () => {
    const saved = { latitude: "-33.8", longitude: "151.2" };
    expect(locationPatch(saved, saved)).toBeUndefined();
    expect(locationPatch(EMPTY_LOCATION, saved)).toEqual({ latitude: null, longitude: null });
    expect(locationPatch({ latitude: "-34", longitude: "151" }, saved)).toEqual({ latitude: -34, longitude: 151 });
    expect(locationPatch({ latitude: "-34", longitude: "" }, saved)).toMatch(/latitude/);
  });
});
