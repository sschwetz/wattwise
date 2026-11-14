import { describe, expect, it } from "vitest";
import { orderFailures, tileTone } from "./alertTone";
import type { ActorFailure, AlertSeverity } from "./types";

function row(severity: AlertSeverity, cleared_at: string | null = null): ActorFailure {
  return {
    kind: "alert", instance_id: null, schedule_id: null, schedule_name: null, message: "m",
    since: "2026-11-13T09:00:00Z", subject: "Site", severity, alert_id: 1, cleared_at, sticky: false,
  };
}

describe("tileTone", () => {
  it("red wins over amber and blue", () => {
    expect(tileTone([row("notice"), row("partial"), row("failure")])).toBe("failure");
  });
  it("amber wins over blue", () => {
    expect(tileTone([row("notice"), row("partial")])).toBe("partial");
  });
  it("blue alone gives notice", () => {
    expect(tileTone([row("notice")])).toBe("notice");
  });
  it("cleared rows are ignored", () => {
    expect(tileTone([row("failure", "2026-11-13T10:00:00Z"), row("partial")])).toBe("partial");
  });
  it("empty list is neutral", () => {
    expect(tileTone([])).toBe("neutral");
  });
});

describe("orderFailures", () => {
  const at = (r: ActorFailure, since: string, id: number): ActorFailure => ({ ...r, since, alert_id: id });

  it("puts unresolved rows by severity, newest first within a severity, cleared last", () => {
    const rows = [
      at(row("notice"), "2026-11-13T09:00:00Z", 1),
      at(row("failure", "2026-11-13T11:00:00Z"), "2026-11-13T10:30:00Z", 2),
      at(row("partial"), "2026-11-13T08:00:00Z", 3),
      at(row("failure"), "2026-11-13T07:00:00Z", 4),
      at(row("failure"), "2026-11-13T10:00:00Z", 5),
    ];
    expect(orderFailures(rows).map((r) => r.alert_id)).toEqual([5, 4, 3, 1, 2]);
  });

  it("does not change the input", () => {
    const rows = [row("notice"), row("failure")];
    orderFailures(rows);
    expect(rows[0].severity).toBe("notice");
  });
});
