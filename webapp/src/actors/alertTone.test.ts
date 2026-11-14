import { describe, expect, it } from "vitest";
import { tileTone } from "./alertTone";
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
