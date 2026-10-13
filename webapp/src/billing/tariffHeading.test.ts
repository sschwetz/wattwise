import { describe, expect, it } from "vitest";
import type { Source } from "../sources/useSources";
import { tariffHeading } from "./tariffHeading";

const NOW = new Date("2026-06-15T00:00:00Z");

function source(overrides: Partial<Source>): Source {
  return {
    id: 1,
    site_id: 7,
    kind: "pricing",
    provider: "amber",
    name: null,
    enabled: true,
    effective_to: null,
    config: {},
    ...overrides,
  };
}

describe("tariffHeading", () => {
  it("is Tariffs before the sources load", () => {
    expect(tariffHeading(undefined, NOW)).toBe("Tariffs");
  });

  it("is Tariffs with no pricing source", () => {
    expect(tariffHeading([source({ kind: "energy", provider: "envoy" })], NOW)).toBe("Tariffs");
  });

  it("is Amber Tariffs for an Amber source, whatever its label", () => {
    expect(tariffHeading([source({ name: "Home" })], NOW)).toBe("Amber Tariffs");
  });

  it("uses the tariff name for a local source", () => {
    expect(tariffHeading([source({ provider: "local", name: "Origin Fixed" })], NOW)).toBe("Origin Fixed Tariffs");
  });

  it("falls back to Tariffs for a local source with a blank name", () => {
    expect(tariffHeading([source({ provider: "local", name: "  " })], NOW)).toBe("Tariffs");
  });

  it("ignores a pricing source that has ended", () => {
    const ended = source({ provider: "local", name: "Old", effective_to: "2026-01-01T00:00:00Z" });
    expect(tariffHeading([ended], NOW)).toBe("Tariffs");
  });

  it("ignores a disabled pricing source", () => {
    expect(tariffHeading([source({ enabled: false })], NOW)).toBe("Tariffs");
  });

  it("uses the active source when an ended one is also listed", () => {
    const ended = source({ id: 1, provider: "amber", effective_to: "2026-01-01T00:00:00Z" });
    const current = source({ id: 2, provider: "local", name: "Origin Fixed" });
    expect(tariffHeading([ended, current], NOW)).toBe("Origin Fixed Tariffs");
  });
});
