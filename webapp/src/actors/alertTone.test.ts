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

  it("sorts needs_sign_in last in its severity, by instance id, whatever its since", () => {
    const signIn = (id: number, since: string): ActorFailure => ({ ...row("failure"), kind: "needs_sign_in", instance_id: id, since });
    const older = at(row("failure"), "2026-01-01T00:00:00Z", 1);
    const a = orderFailures([signIn(2, "2026-11-14T10:00:01Z"), signIn(1, "2026-11-14T10:00:02Z"), older]);
    const b = orderFailures([signIn(2, "2026-11-14T10:00:20Z"), signIn(1, "2026-11-14T10:00:19Z"), older]);
    expect(a.map((r) => r.instance_id)).toEqual([null, 1, 2]);
    expect(b.map((r) => r.instance_id)).toEqual([null, 1, 2]);
  });

  it("keeps input order for equal since and survives an invalid date", () => {
    const rows = [at(row("failure"), "2026-11-13T09:00:00Z", 1), at(row("failure"), "2026-11-13T09:00:00Z", 2), at(row("failure"), "nonsense", 3)];
    expect(orderFailures(rows).map((r) => r.alert_id)).toEqual([1, 2, 3]);
  });
});
